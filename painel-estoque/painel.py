#!/usr/bin/env python3
# versao-do-painel: 8
"""Painel de Estoque - cadastro central (Casa de Embalagens Cangaíba).

Roda só neste computador (127.0.0.1). Usa apenas a biblioteca padrão do Python.

Uso:
    python painel.py                      abre o painel no navegador
    python painel.py importar-gestorz ARQ.csv
    python painel.py importar-painel ARQ.csv
    python painel.py sync-nuvemshop       lê os produtos da Nuvemshop (somente leitura)
    python painel.py definir-senha        cria ou troca a senha de acesso

Acesso pelo celular (fora deste computador): defina uma senha (PAINEL_SENHA ou "senha" no
config.json) e PAINEL_HOST=0.0.0.0. Sem senha o painel só responde neste computador.

Este programa NÃO altera nada na Nuvemshop, no iFood, na 99Food nem no Gestor Z.
"""
import base64
import collections
import csv
import difflib
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
import unicodedata
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("PAINEL_DB", os.path.join(BASE, "banco.db"))
CONFIG_PATH = os.environ.get("PAINEL_CONFIG", os.path.join(BASE, "config.json"))
API_BASE = os.environ.get("NUVEMSHOP_API_BASE", "https://api.tiendanube.com/v1").rstrip("/")
STATIC = os.path.join(BASE, "static")
PORTA = int(os.environ.get("PAINEL_PORTA") or os.environ.get("PORT") or "8765")
HOST = os.environ.get("PAINEL_HOST", "127.0.0.1")
SESSAO_DIAS = 30
VERSAO_SERVIDOR = "8"      # a tela compara com este número para detectar janela antiga aberta

CANAIS = ("nuvemshop", "ifood", "99food")

SCHEMA = """
CREATE TABLE IF NOT EXISTS produtos (
    id INTEGER PRIMARY KEY,
    sku TEXT,                -- id_produto do Gestor Z = SKU da Nuvemshop
    gz_codigo TEXT,          -- código interno do Gestor Z
    ean TEXT,
    ean_norm TEXT,           -- código de barras sem zeros à esquerda
    nome TEXT NOT NULL,
    grupo TEXT,
    unidade TEXT,
    custo REAL,
    preco REAL,              -- preço de venda no caixa (base das regras de preço)
    ativo INTEGER DEFAULT 1,
    origem TEXT,             -- gestorz | painel | nuvemshop
    estoque INTEGER,         -- estoque oficial; NULL = ainda não conferido
    conferido_em TEXT,
    gz_saldo TEXT,
    situacao TEXT,
    acao TEXT,
    vendas_ano REAL,
    ultima_compra TEXT,
    obs TEXT,
    foto_url TEXT,           -- foto do produto no site (Nuvemshop)
    atualizado_em TEXT,
    estimado REAL,           -- estoque estimado = contagem + entradas - vendas (calculado)
    est_aviso TEXT,          -- 'peso' = vende por KG, não estimamos
    categoria_id TEXT,       -- categoria da loja (Nuvemshop)
    categoria_nome TEXT
);
CREATE INDEX IF NOT EXISTS ix_prod_sku ON produtos(sku);
CREATE INDEX IF NOT EXISTS ix_prod_gz ON produtos(gz_codigo);
CREATE INDEX IF NOT EXISTS ix_prod_ean ON produtos(ean_norm);

CREATE TABLE IF NOT EXISTS canais (
    produto_id INTEGER NOT NULL REFERENCES produtos(id),
    canal TEXT NOT NULL,
    id_variacao TEXT NOT NULL DEFAULT '',
    id_externo TEXT,
    nome TEXT,
    preco REAL,
    estoque TEXT,            -- texto: número, '' (sem dado) ou 'ilimitado'
    status TEXT,
    vendendo INTEGER,        -- 1 = o cliente consegue comprar hoje
    atualizado_em TEXT,
    PRIMARY KEY (produto_id, canal, id_variacao)
);
CREATE INDEX IF NOT EXISTS ix_canais_canal ON canais(canal);

CREATE TABLE IF NOT EXISTS historico (
    id INTEGER PRIMARY KEY,
    produto_id INTEGER NOT NULL,
    quando TEXT NOT NULL,
    campo TEXT NOT NULL,
    de TEXT,
    para TEXT,
    origem TEXT
);
CREATE INDEX IF NOT EXISTS ix_hist_prod ON historico(produto_id);

CREATE TABLE IF NOT EXISTS meta (chave TEXT PRIMARY KEY, valor TEXT);

-- fotos tiradas na prateleira (arquivo na pasta fotos, ao lado do banco)
CREATE TABLE IF NOT EXISTS fotos (
    produto_id INTEGER PRIMARY KEY REFERENCES produtos(id),
    quando TEXT NOT NULL,
    quem TEXT
);

-- importações de vendas e de notas (para conferir e poder remover)
CREATE TABLE IF NOT EXISTS importacoes (
    id INTEGER PRIMARY KEY,
    tipo TEXT NOT NULL,      -- vendas | notas
    inicio TEXT, fim TEXT,
    arquivo TEXT,
    linhas INTEGER, novas INTEGER,
    quando TEXT NOT NULL, quem TEXT
);

-- vendas do caixa (Gestor Z), um período por importação
CREATE TABLE IF NOT EXISTS vendas_gz (
    id INTEGER PRIMARY KEY,
    imp_id INTEGER NOT NULL,
    inicio TEXT NOT NULL, fim TEXT NOT NULL,
    gz_codigo TEXT NOT NULL,
    nome TEXT, qtd REAL, unidade TEXT, valor REAL
);
CREATE INDEX IF NOT EXISTS ix_vgz_cod ON vendas_gz(gz_codigo);
CREATE INDEX IF NOT EXISTS ix_vgz_imp ON vendas_gz(imp_id);

-- itens das notas fiscais de entrada (fornecedores)
CREATE TABLE IF NOT EXISTS notas_itens (
    id INTEGER PRIMARY KEY,
    chave TEXT UNIQUE NOT NULL,
    imp_id INTEGER,
    emissao TEXT, fornecedor TEXT, cnpj TEXT, nota TEXT, serie TEXT,
    cod_fornecedor TEXT, descricao TEXT, un TEXT, quantidade REAL,
    cfop TEXT, ncm TEXT
);
CREATE INDEX IF NOT EXISTS ix_ni_item ON notas_itens(cnpj, cod_fornecedor);

-- ligação item do fornecedor -> produto, com unidades por caixa/pacote
CREATE TABLE IF NOT EXISTS de_para (
    cnpj TEXT NOT NULL, cod_fornecedor TEXT NOT NULL,
    produto_id INTEGER,
    fator REAL,
    estado TEXT NOT NULL,    -- confirmado | ignorar
    quem TEXT, quando TEXT,
    PRIMARY KEY (cnpj, cod_fornecedor)
);

-- categorias da loja (lidas da Nuvemshop)
CREATE TABLE IF NOT EXISTS categorias_site (
    id TEXT PRIMARY KEY,
    nome TEXT NOT NULL       -- caminho completo: Pai > Filha
);

-- grupo do Gestor Z -> categoria da loja (Nuvemshop)
CREATE TABLE IF NOT EXISTS categoria_map (
    grupo TEXT PRIMARY KEY,
    categoria_id TEXT NOT NULL,
    categoria_nome TEXT
);

-- códigos bipados na prateleira que não existem no cadastro
CREATE TABLE IF NOT EXISTS leituras_sem_cadastro (
    id INTEGER PRIMARY KEY,
    codigo TEXT NOT NULL,
    quando TEXT NOT NULL,
    quem TEXT,
    obs TEXT
);
"""


def agora():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def fotos_dir():
    return os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "fotos")


def conectar():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    colunas = {r["name"] for r in con.execute("PRAGMA table_info(produtos)")}
    if "foto_url" not in colunas:          # banco criado por uma versão anterior
        con.execute("ALTER TABLE produtos ADD COLUMN foto_url TEXT")
    for col, tipo in (("estimado", "REAL"), ("est_aviso", "TEXT"), ("categoria_id", "TEXT"),
                      ("categoria_nome", "TEXT")):
        if col not in colunas:
            con.execute(f"ALTER TABLE produtos ADD COLUMN {col} {tipo}")
        con.commit()
    return con


def norm_ean(v):
    v = "".join(ch for ch in str(v or "") if ch.isdigit())
    return v.lstrip("0")


def num(v):
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


def set_meta(con, chave, valor):
    con.execute("INSERT INTO meta(chave,valor) VALUES(?,?) "
                "ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor", (chave, str(valor)))


def ler_csv(origem):
    """Aceita caminho de arquivo ou bytes (upload)."""
    if isinstance(origem, (bytes, bytearray)):
        dados = bytes(origem)
    else:
        with open(origem, "rb") as f:
            dados = f.read()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            texto = dados.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    amostra = texto[:4000]
    delim = ";" if amostra.count(";") > amostra.count(",") else ","
    return list(csv.DictReader(io.StringIO(texto), delimiter=delim))


# ---------------------------------------------------------------- importações

def importar_gestorz(con, origem):
    """Cadastro de produtos do Gestor Z (colunas id_produto, codigo, gtin, descricao...)."""
    linhas = ler_csv(origem)
    if not linhas or "id_produto" not in linhas[0] or "descricao" not in linhas[0]:
        raise ValueError("Arquivo não parece o cadastro de produtos do Gestor Z "
                         "(faltam as colunas id_produto e descricao).")
    novos = atualizados = 0
    for l in linhas:
        sku = (l.get("id_produto") or "").strip()
        if not sku or sku.startswith("-"):   # lançamentos internos (gorjeta, taxas)
            continue
        campos = dict(
            sku=sku, gz_codigo=(l.get("codigo") or "").strip(),
            ean=(l.get("gtin") or "").strip(), ean_norm=norm_ean(l.get("gtin")),
            nome=(l.get("descricao") or "").strip(), grupo=(l.get("grupo") or "").strip(),
            unidade=(l.get("un") or "").strip(), custo=num(l.get("custo")),
            preco=num(l.get("venda")), ativo=0 if (l.get("ativo") or "S").upper() == "N" else 1,
            atualizado_em=agora())
        r = con.execute("SELECT id FROM produtos WHERE sku=?", (sku,)).fetchone()
        if r is None and campos["gz_codigo"]:
            r = con.execute("SELECT id FROM produtos WHERE gz_codigo=? AND sku IS NULL",
                            (campos["gz_codigo"],)).fetchone()
        if r is None and campos["ean_norm"]:
            r = con.execute("SELECT id FROM produtos WHERE origem='manual' AND ean_norm=? ORDER BY id LIMIT 1",
                            (campos["ean_norm"],)).fetchone()
        if r:
            sets = ",".join(f"{k}=:{k}" for k in campos)
            con.execute(f"UPDATE produtos SET {sets}, origem='gestorz' WHERE id=:id",
                        dict(campos, id=r["id"]))
            atualizados += 1
        else:
            cols = ",".join(campos)
            vals = ",".join(":" + k for k in campos)
            con.execute(f"INSERT INTO produtos({cols},origem) VALUES({vals},'gestorz')", campos)
            novos += 1
    set_meta(con, "gestorz_importado_em", agora())
    con.commit()
    return {"lidos": len(linhas), "novos": novos, "atualizados": atualizados}


def _vendendo_ifood(s):
    return 1 if s == "Em venda" else 0


def _vendendo_99(s):
    return 1 if s == "Ativo com estoque" else 0


def importar_painel(con, origem):
    """Cruzamento dos 3 canais (colunas ean, produto, gz_codigo, ifood_status, n99_status...)."""
    linhas = ler_csv(origem)
    if not linhas or "ifood_status" not in linhas[0] or "n99_status" not in linhas[0]:
        raise ValueError("Arquivo não parece o cruzamento de estoque dos 3 canais "
                         "(faltam as colunas ifood_status e n99_status).")
    casados = criados = 0
    for l in linhas:
        gz = (l.get("gz_codigo") or "").strip()
        ean_n = norm_ean(l.get("ean"))
        r = None
        if gz:
            r = con.execute("SELECT id FROM produtos WHERE gz_codigo=?", (gz,)).fetchone()
        if r is None and ean_n:
            r = con.execute("SELECT id FROM produtos WHERE ean_norm=? ORDER BY id LIMIT 1",
                            (ean_n,)).fetchone()
        if r is None:
            cur = con.execute(
                "INSERT INTO produtos(gz_codigo,ean,ean_norm,nome,grupo,origem,atualizado_em) "
                "VALUES(?,?,?,?,?,'painel',?)",
                (gz or None, (l.get("ean") or "").strip(), ean_n, (l.get("produto") or "").strip(),
                 (l.get("categoria") or "").strip(), agora()))
            pid = cur.lastrowid
            criados += 1
        else:
            pid = r["id"]
            casados += 1
        con.execute("UPDATE produtos SET gz_saldo=?, situacao=?, acao=?, vendas_ano=?, "
                    "ultima_compra=? WHERE id=?",
                    ((l.get("gz_saldo") or "").strip(), (l.get("situacao") or "").strip(),
                     (l.get("acao") or "").strip(), num(l.get("vendas_ano")),
                     (l.get("ultima_compra") or "").strip(), pid))
        ifs = (l.get("ifood_status") or "").strip()
        n99 = (l.get("n99_status") or "").strip()
        for canal, status, ausente, estoque, vend in (
                ("ifood", ifs, "Não está no iFood", "", _vendendo_ifood(ifs)),
                ("99food", n99, "Não está no 99", (l.get("n99_estoque") or "").strip(),
                 _vendendo_99(n99))):
            con.execute("DELETE FROM canais WHERE produto_id=? AND canal=?", (pid, canal))
            if status and status != ausente:
                con.execute(
                    "INSERT INTO canais(produto_id,canal,id_variacao,status,estoque,vendendo,"
                    "atualizado_em) VALUES(?,?,'',?,?,?,?)",
                    (pid, canal, status, estoque, vend, agora()))
    set_meta(con, "painel_importado_em", agora())
    con.commit()
    recalcular(con)
    return {"lidos": len(linhas), "casados": casados, "criados": criados}


# ------------------------------------------------------------------ Nuvemshop

def carregar_config():
    cfg = {}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
    return {
        "senha": os.environ.get("PAINEL_SENHA") or cfg.get("senha", ""),
        "senha_hash": cfg.get("senha_hash", ""),
        "token": os.environ.get("NUVEMSHOP_ACCESS_TOKEN") or cfg.get("nuvemshop_token", ""),
        "loja": os.environ.get("NUVEMSHOP_STORE_ID") or str(cfg.get("nuvemshop_loja", "")),
        "agente": os.environ.get("NUVEMSHOP_USER_AGENT")
        or cfg.get("nuvemshop_user_agent", "Painel de Estoque"),
    }


def _get_nuvemshop(cfg, pagina):
    url = f"{API_BASE}/{cfg['loja']}/products?per_page=200&page={pagina}"
    req = urllib.request.Request(url, headers={
        "Authentication": "bearer " + cfg["token"], "User-Agent": cfg["agente"]})
    espera = 3
    for _ in range(6):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code == 404:          # página depois da última
                return None
            if e.code == 429 or e.code >= 500:
                time.sleep(espera)
                espera *= 2
                continue
            if e.code in (401, 403):
                raise RuntimeError("A Nuvemshop recusou o acesso (token inválido ou sem permissão).")
            raise
    raise RuntimeError("A Nuvemshop não respondeu depois de várias tentativas.")


def _texto(v):
    if isinstance(v, dict):
        return v.get("pt") or next(iter(v.values()), "") or ""
    return v or ""


def sync_nuvemshop(con, progresso=None, completo=False):
    """Lê todos os produtos da Nuvemshop (GET). Não escreve nada na loja."""
    cfg = carregar_config()
    if not cfg["token"] or not cfg["loja"]:
        raise RuntimeError("Falta o acesso da Nuvemshop. Preencha o arquivo config.json "
                           "(veja config.exemplo.json).")
    produtos, pagina = [], 1
    while True:
        lote = _get_nuvemshop(cfg, pagina)
        if not lote:
            break
        produtos.extend(lote)
        if progresso:
            progresso(f"Lendo a Nuvemshop: {len(produtos)} produtos")
        if len(lote) < 200:
            break
        pagina += 1
        time.sleep(1.5)

    con.execute("DELETE FROM canais WHERE canal='nuvemshop'")
    con.execute("UPDATE produtos SET foto_url=NULL")       # a foto do site é relida a cada leitura
    con.execute("UPDATE produtos SET categoria_id=NULL, categoria_nome=NULL WHERE origem<>'manual'")
    vistas = {}                                            # categorias vistas nos produtos: id -> (nome, pai)
    for p in produtos:
        for c in p.get("categories") or []:
            vistas[str(c.get("id"))] = (_texto(c.get("name")), str(c["parent"]) if c.get("parent") else None)
    if completo:                                           # lista completa (inclui categorias vazias), se o token deixar
        try:
            for c in categorias_nuvemshop_cruas(cfg):
                vistas.setdefault(str(c.get("id")), (_texto(c.get("name")), str(c["parent"]) if c.get("parent") else None))
        except Exception:  # noqa: BLE001 - sem permissão: ficamos só com as que os produtos mostram
            pass

    def caminho(cid, n=0):
        nome, pai = vistas.get(cid, ("", None))
        return (caminho(pai, n + 1) + " > " if pai and pai in vistas and n < 6 else "") + nome
    caminhos = {cid: caminho(cid) for cid in vistas}
    if completo:
        con.execute("DELETE FROM categorias_site")
    con.executemany("INSERT OR REPLACE INTO categorias_site(id, nome) VALUES(?,?)", list(caminhos.items()))
    por_sku = por_ean = criados = variacoes = 0
    for p in produtos:
        nome = _texto(p.get("name"))
        publicado = bool(p.get("published"))
        imagens = sorted(p.get("images") or [], key=lambda i: i.get("position") or 0)
        src_por_id = {i.get("id"): i.get("src") for i in imagens}
        src_primeira = imagens[0].get("src") if imagens else None
        for v in p.get("variants") or []:
            variacoes += 1
            sku = (v.get("sku") or "").strip()
            ean_n = norm_ean(v.get("barcode"))
            valores = " ".join(_texto(x) for x in (v.get("values") or []) if _texto(x))
            nome_v = f"{nome} ({valores})" if valores else nome
            r = None
            if sku:
                r = con.execute("SELECT id FROM produtos WHERE sku=?", (sku,)).fetchone()
                if r:
                    por_sku += 1
            if r is None and ean_n:
                r = con.execute("SELECT id FROM produtos WHERE ean_norm=? ORDER BY id LIMIT 1",
                                (ean_n,)).fetchone()
                if r:
                    por_ean += 1
            if r is None and not sku and not ean_n:
                r = con.execute("SELECT id FROM produtos WHERE origem='nuvemshop' AND nome=?",
                                (nome_v,)).fetchone()
            if r is None:
                cur = con.execute(
                    "INSERT INTO produtos(sku,ean,ean_norm,nome,preco,origem,atualizado_em) "
                    "VALUES(?,?,?,?,?,'nuvemshop',?)",
                    (sku or None, (v.get("barcode") or "").strip(), ean_n, nome_v,
                     num(v.get("price")), agora()))
                pid = cur.lastrowid
                criados += 1
            else:
                pid = r["id"]
            cids = [str(c.get("id")) for c in (p.get("categories") or [])]
            if cids:
                fundo = max(cids, key=lambda i: caminhos.get(i, "").count(">"))
                con.execute("UPDATE produtos SET categoria_id=?, categoria_nome=? WHERE id=?",
                            (fundo, caminhos.get(fundo, ""), pid))
            src = src_por_id.get(v.get("image_id")) or src_primeira
            if src:
                con.execute("UPDATE produtos SET foto_url=? WHERE id=?", (src, pid))
            if not v.get("stock_management") or v.get("stock") is None:
                estoque, tem = "ilimitado", True
            else:
                estoque, tem = str(v.get("stock")), (v.get("stock") or 0) > 0
            con.execute(
                "INSERT OR REPLACE INTO canais(produto_id,canal,id_variacao,id_externo,nome,preco,"
                "estoque,status,vendendo,atualizado_em) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (pid, "nuvemshop", str(v.get("id")), str(p.get("id")), nome_v,
                 num(v.get("price")), estoque, "Publicado" if publicado else "Oculto",
                 1 if (publicado and tem) else 0, agora()))
    set_meta(con, "nuvemshop_sync_em", agora())
    con.commit()
    return {"produtos": len(produtos), "variacoes": variacoes, "por_sku": por_sku,
            "por_ean": por_ean, "sem_par": criados}


# ------------------------------------------------- envio de estoque para a Nuvemshop
# Regras: nunca grava sem prévia + confirmação; sempre guarda backup; dá para desfazer.
MAX_POR_ENVIO = 300


def backups_dir():
    return os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "backups")


def _nuvem(cfg, metodo, caminho, corpo=None):
    """Chamada à API da Nuvemshop, com espera e nova tentativa no limite de requisições."""
    url = f"{API_BASE}/{cfg['loja']}{caminho}"
    dados = json.dumps(corpo).encode() if corpo is not None else None
    espera = 3
    for _ in range(6):
        req = urllib.request.Request(url, data=dados, method=metodo, headers={
            "Authentication": "bearer " + cfg["token"], "User-Agent": cfg["agente"],
            "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                bruto = resp.read()
                return json.loads(bruto) if bruto.strip() else {}
        except urllib.error.HTTPError as e:
            detalhe = e.read().decode("utf-8", "replace")[:300]
            if e.code == 429 or e.code >= 500:
                time.sleep(espera)
                espera *= 2
                continue
            if e.code in (401, 403):
                raise RuntimeError("A Nuvemshop recusou o acesso (token inválido ou sem permissão "
                                   "para gravar produtos).")
            raise RuntimeError(f"A Nuvemshop respondeu {e.code}: {detalhe}")
    raise RuntimeError("A Nuvemshop não respondeu depois de várias tentativas.")


def _exigir_nuvemshop():
    cfg = carregar_config()
    if not cfg["token"] or not cfg["loja"]:
        raise RuntimeError("A Nuvemshop ainda não está conectada. Informe o token em "
                           "“Enviar para a Nuvemshop”.")
    return cfg


def conectar_nuvemshop(token, loja):
    """Confere o token (só leitura) e o guarda no config.json, neste computador."""
    token = (token or "").strip()
    loja = "".join(c for c in str(loja or "") if c.isdigit())
    if len(token) < 20 or not loja:
        raise ValueError("Informe o token (código longo) e o número da loja.")
    cfg = {"token": token, "loja": loja, "agente": carregar_config()["agente"]}
    try:
        info = _nuvem(cfg, "GET", "/store")
    except RuntimeError as e:
        raise ValueError(str(e))
    dados = {}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            dados = json.load(f)
    dados["nuvemshop_token"], dados["nuvemshop_loja"] = token, loja
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(dados, f, indent=2, ensure_ascii=False)
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass
    return {"loja": _texto(info.get("name")) or "loja conectada"}


def previa_nuvemshop(con, modo="zerar"):
    """O que mudaria no site com base no estoque contado. Não altera nada."""
    modo = "repor" if modo == "repor" else "zerar"
    itens, ja_igual, varias = [], 0, {}
    for r in con.execute(
            "SELECT p.id pid, p.nome, p.sku, p.estoque, k.id_externo prod_site, k.id_variacao var_site, "
            "k.estoque site, k.status, m.n FROM produtos p JOIN canais k ON k.produto_id=p.id "
            "AND k.canal='nuvemshop' JOIN (SELECT produto_id, COUNT(*) n FROM canais WHERE canal='nuvemshop' "
            "GROUP BY produto_id) m ON m.produto_id=p.id WHERE p.estoque IS NOT NULL ORDER BY p.nome"):
        if r["n"] > 1:
            # um produto contado ligado a várias variações do site (cores, tamanhos): não dá para
            # saber qual variação a contagem representa, então fica para decidir à mão
            varias[r["pid"]] = r["nome"]
            continue
        site, contado = (r["site"] or "").strip(), r["estoque"]
        if contado == 0:
            alvo, motivo = (None, None) if site == "0" else (0, "acabou na prateleira")
        elif modo == "repor" and site != str(contado):
            alvo, motivo = contado, "quantidade contada"
        else:
            alvo, motivo = None, None
        if alvo is None:
            ja_igual += 1
            continue
        itens.append({"pid": r["pid"], "nome": r["nome"], "sku": r["sku"], "prod_site": int(r["prod_site"]),
                      "var_site": int(r["var_site"]), "de": site or "ilimitado", "para": alvo,
                      "publicado": r["status"] == "Publicado", "motivo": motivo})
    conferidos = con.execute("SELECT COUNT(*) FROM produtos WHERE estoque IS NOT NULL").fetchone()[0]
    sem_loja = con.execute("SELECT COUNT(*) FROM produtos p WHERE p.estoque IS NOT NULL AND NOT EXISTS "
                           "(SELECT 1 FROM canais k WHERE k.produto_id=p.id AND k.canal='nuvemshop')").fetchone()[0]
    return {"modo": modo, "conferidos": conferidos, "mudancas": len(itens), "ja_certos": ja_igual,
            "sem_loja": sem_loja, "varias_variacoes": len(varias), "revisar": sorted(varias.values())[:15],
            "limite": MAX_POR_ENVIO, "itens": itens}


def _gravar_estoques(cfg, grupos, progresso):
    """grupos: lista de (produto_site, variacao_site, valor) com valor int, ou "" para ilimitado.
    Devolve {variacao: erro ou None}."""
    resultado = {}
    numericos = [g for g in grupos if g[2] != ""]
    ilimitados = [g for g in grupos if g[2] == ""]
    lotes, atual, por_produto, n = [], [], {}, 0
    for prod, var, valor in numericos:                     # até 50 variações por chamada
        if n >= 50:
            lotes.append(atual); atual, por_produto, n = [], {}, 0
        if prod not in por_produto:
            por_produto[prod] = {"id": prod, "variants": []}
            atual.append(por_produto[prod])
        por_produto[prod]["variants"].append({"id": var, "inventory_levels": [{"stock": valor}]})
        n += 1
    if atual:
        lotes.append(atual)
    feitos = 0
    for lote in lotes:
        resp = _nuvem(cfg, "PATCH", "/products/stock-price", lote)
        ok = {}
        for prod in (resp if isinstance(resp, list) else []):
            for v in prod.get("variants") or []:
                ok[v.get("id")] = None if v.get("success") else (str(v.get("errors") or "recusado")[:120])
        for prod in lote:
            for v in prod["variants"]:
                resultado[v["id"]] = ok.get(v["id"], "sem resposta da loja")
        feitos += sum(len(p["variants"]) for p in lote)
        progresso(f"Gravando na loja: {feitos} de {len(grupos)}")
        time.sleep(1.5)
    for prod, var, _ in ilimitados:                        # voltar ao ilimitado: uma por vez
        try:
            _nuvem(cfg, "PUT", f"/products/{prod}/variants/{var}", {"inventory_levels": [{"stock": ""}]})
            resultado[var] = None
        except RuntimeError as e:
            resultado[var] = str(e)[:120]
        feitos += 1
        progresso(f"Gravando na loja: {feitos} de {len(grupos)}")
        time.sleep(1.5)
    return resultado


def _estado_site(con):
    return {int(r["id_variacao"]): (r["estoque"] or "") for r in
            con.execute("SELECT id_variacao, estoque FROM canais WHERE canal='nuvemshop'")}


def _salvar_backup(prefixo, linhas):
    os.makedirs(backups_dir(), exist_ok=True)
    nome = f"{prefixo}_{datetime.now():%Y%m%d_%H%M%S}.csv"
    with open(os.path.join(backups_dir(), nome), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["produto_site", "variacao_site", "sku", "nome", "antes", "para"])
        w.writerows(linhas)
    return nome


def listar_backups():
    if not os.path.isdir(backups_dir()):
        return []
    saida = []
    for nome in sorted(os.listdir(backups_dir()), reverse=True):
        if nome.startswith("nuvemshop_") and nome.endswith(".csv") and not nome.endswith(".desfeito.csv"):
            caminho = os.path.join(backups_dir(), nome)
            with open(caminho, encoding="utf-8-sig") as f:
                qtd = max(0, sum(1 for _ in f) - 1)
            saida.append({"arquivo": nome, "quando": datetime.fromtimestamp(os.path.getmtime(caminho)).strftime("%Y-%m-%d %H:%M"),
                          "itens": qtd, "desfazer": not nome.startswith("nuvemshop_desfazer_")})
    return saida


def aplicar_nuvemshop(modo, vids, quem, progresso):
    cfg = _exigir_nuvemshop()
    quer = {int(v) for v in vids}
    if not quer:
        raise RuntimeError("Nada para gravar.")
    if len(quer) > MAX_POR_ENVIO:
        raise RuntimeError(f"Por segurança, grave no máximo {MAX_POR_ENVIO} por vez.")
    con = conectar()
    try:
        progresso("Lendo a loja para conferir o estado atual…")
        sync_nuvemshop(con)
        plano = {i["var_site"]: i for i in previa_nuvemshop(con, modo)["itens"]}
        escolhidos = [plano[v] for v in quer if v in plano]
        pulados = len(quer) - len(escolhidos)          # mudaram desde a prévia
        if not escolhidos:
            return {"gravados": 0, "pulados": pulados, "falharam": [], "backup": None}
        backup = _salvar_backup("nuvemshop", [[i["prod_site"], i["var_site"], i["sku"] or "", i["nome"],
                                               i["de"], i["para"]] for i in escolhidos])
        resultado = _gravar_estoques(cfg, [(i["prod_site"], i["var_site"], i["para"]) for i in escolhidos], progresso)
        progresso("Conferindo na loja o que foi gravado…")
        sync_nuvemshop(con)
        depois = _estado_site(con)
        falharam, gravados = [], 0
        for i in escolhidos:
            if resultado.get(i["var_site"]) is None and depois.get(i["var_site"]) == str(i["para"]):
                gravados += 1
                con.execute("INSERT INTO historico(produto_id,quando,campo,de,para,origem) VALUES(?,?,?,?,?,?)",
                            (i["pid"], agora(), "estoque no site", i["de"], str(i["para"]), quem or "painel"))
            else:
                falharam.append(f"{i['nome']}: " + (resultado.get(i["var_site"]) or "o site não mostra o valor esperado"))
        con.commit()
        return {"gravados": gravados, "pulados": pulados, "falharam": falharam[:10], "backup": backup}
    finally:
        con.close()


def _ler_backup(arquivo):
    permitidos = {b["arquivo"] for b in listar_backups() if b["desfazer"]}
    if arquivo not in permitidos:
        raise RuntimeError("Backup não encontrado.")
    with open(os.path.join(backups_dir(), arquivo), encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def desfazer_nuvemshop(arquivo, quem, progresso):
    cfg = _exigir_nuvemshop()
    linhas = _ler_backup(arquivo)
    con = conectar()
    try:
        progresso("Lendo a loja para conferir o estado atual…")
        sync_nuvemshop(con)
        atual = _estado_site(con)
        restaurar, pulados = [], 0
        for l in linhas:
            var = int(l["variacao_site"])
            if atual.get(var) != str(l["para"]):        # mudou depois da gravação: não mexo
                pulados += 1
                continue
            restaurar.append((int(l["produto_site"]), var, "" if l["antes"] == "ilimitado" else int(l["antes"]), l))
        if not restaurar:
            return {"gravados": 0, "pulados": pulados, "falharam": [], "backup": None}
        backup = _salvar_backup("nuvemshop_desfazer", [[p, v, l["sku"], l["nome"], atual.get(v, ""),
                                                        "ilimitado" if val == "" else val] for p, v, val, l in restaurar])
        resultado = _gravar_estoques(cfg, [(p, v, val) for p, v, val, _ in restaurar], progresso)
        progresso("Conferindo na loja o que foi desfeito…")
        sync_nuvemshop(con)
        depois = _estado_site(con)
        falharam, feitos = [], 0
        for p, v, val, l in restaurar:
            esperado = "ilimitado" if val == "" else str(val)
            if resultado.get(v) is None and depois.get(v) == esperado:
                feitos += 1
            else:
                falharam.append(f"{l['nome']}: " + (resultado.get(v) or "o site não mostra o valor esperado"))
        if not falharam and not pulados:
            os.replace(os.path.join(backups_dir(), arquivo), os.path.join(backups_dir(), arquivo[:-4] + ".desfeito.csv"))
        return {"gravados": feitos, "pulados": pulados, "falharam": falharam[:10], "backup": backup}
    finally:
        con.close()


# ------------------------------------------------ envio de fotos para a Nuvemshop

MAX_FOTOS_ENVIO = 40


def previa_fotos(con):
    """Fotos tiradas na prateleira que podem subir: só para produto que está no site e
    ainda não tem foto lá. Não altera nada."""
    por_site = collections.Counter()
    linhas = con.execute(
        "SELECT p.id pid, p.nome, p.sku, p.foto_url, k.id_externo prod_site, k.status "
        "FROM fotos f JOIN produtos p ON p.id=f.produto_id JOIN canais k ON k.produto_id=p.id "
        "AND k.canal='nuvemshop' GROUP BY p.id ORDER BY p.nome").fetchall()
    for r in linhas:
        por_site[r["prod_site"]] += 1
    itens, ja_tem, ambiguas, sem_arquivo = [], 0, [], 0
    for r in linhas:
        if r["foto_url"]:
            ja_tem += 1
        elif por_site[r["prod_site"]] > 1:
            ambiguas.append(r["nome"])
        elif not os.path.exists(os.path.join(fotos_dir(), f"{r['pid']}.jpg")):
            sem_arquivo += 1
        else:
            itens.append({"pid": r["pid"], "nome": r["nome"], "sku": r["sku"],
                          "prod_site": int(r["prod_site"]), "publicado": r["status"] == "Publicado",
                          "foto": f"/api/foto/{r['pid']}"})
    total = con.execute("SELECT COUNT(*) FROM fotos").fetchone()[0]
    fora_site = con.execute(
        "SELECT COUNT(*) FROM fotos f WHERE NOT EXISTS (SELECT 1 FROM canais k WHERE "
        "k.produto_id=f.produto_id AND k.canal='nuvemshop')").fetchone()[0]
    return {"fotos_tiradas": total, "para_enviar": len(itens), "ja_tem_foto_no_site": ja_tem,
            "fora_do_site": fora_site, "ambiguas": len(ambiguas), "revisar": sorted(ambiguas)[:10],
            "limite": MAX_FOTOS_ENVIO, "itens": itens}


def _salvar_backup_fotos(linhas):
    os.makedirs(backups_dir(), exist_ok=True)
    nome = f"fotosnuvem_{datetime.now():%Y%m%d_%H%M%S}.csv"
    with open(os.path.join(backups_dir(), nome), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["produto_site", "imagem_site", "sku", "nome"])
        w.writerows(linhas)
    return nome


def listar_backups_fotos():
    if not os.path.isdir(backups_dir()):
        return []
    saida = []
    for nome in sorted(os.listdir(backups_dir()), reverse=True):
        if nome.startswith("fotosnuvem_") and nome.endswith(".csv") and not nome.endswith(".desfeito.csv"):
            caminho = os.path.join(backups_dir(), nome)
            with open(caminho, encoding="utf-8-sig") as f:
                qtd = max(0, sum(1 for _ in f) - 1)
            saida.append({"arquivo": nome, "itens": qtd, "quando": datetime.fromtimestamp(
                os.path.getmtime(caminho)).strftime("%Y-%m-%d %H:%M")})
    return saida


def aplicar_fotos(pids, quem, progresso):
    cfg = _exigir_nuvemshop()
    quer = {int(p) for p in pids}
    if not quer:
        raise RuntimeError("Nada para enviar.")
    if len(quer) > MAX_FOTOS_ENVIO:
        raise RuntimeError(f"Por segurança, envie no máximo {MAX_FOTOS_ENVIO} fotos por vez.")
    con = conectar()
    try:
        progresso("Lendo a loja para conferir quais produtos ainda estão sem foto…")
        sync_nuvemshop(con)
        plano = {i["pid"]: i for i in previa_fotos(con)["itens"]}
        escolhidos = [plano[p] for p in quer if p in plano]
        pulados = len(quer) - len(escolhidos)         # já ganharam foto ou mudaram desde a prévia
        enviados, falharam = [], []
        for n, i in enumerate(sorted(escolhidos, key=lambda x: x["nome"]), 1):
            progresso(f"Enviando foto {n} de {len(escolhidos)}: {i['nome'][:40]}")
            try:
                with open(os.path.join(fotos_dir(), f"{i['pid']}.jpg"), "rb") as f:
                    b64 = base64.b64encode(f.read()).decode()
                r = _nuvem(cfg, "POST", f"/products/{i['prod_site']}/images", {
                    "filename": f"{(i['sku'] or 'produto-' + str(i['pid']))}.jpg",
                    "position": 1, "attachment": b64})
                if not r.get("id"):
                    raise RuntimeError("a loja não devolveu o número da imagem")
                enviados.append(i | {"imagem": r["id"]})
            except Exception as e:  # noqa: BLE001
                falharam.append(f"{i['nome']}: {e}")
            time.sleep(0.6)
        backup = _salvar_backup_fotos([[i["prod_site"], i["imagem"], i["sku"] or "", i["nome"]]
                                       for i in enviados]) if enviados else None
        progresso("Conferindo na loja o que foi enviado…")
        sync_nuvemshop(con)
        ok = 0
        for i in enviados:
            if con.execute("SELECT foto_url FROM produtos WHERE id=?", (i["pid"],)).fetchone()[0]:
                ok += 1
                con.execute("INSERT INTO historico(produto_id,quando,campo,de,para,origem) "
                            "VALUES(?,?,?,?,?,?)", (i["pid"], agora(), "foto no site", "",
                                                    "foto enviada para a Nuvemshop", quem or "painel"))
            else:
                falharam.append(f"{i['nome']}: a loja ainda não mostra a foto (confira no site)")
        con.commit()
        return {"gravados": ok, "pulados": pulados, "falharam": falharam[:10], "backup": backup}
    finally:
        con.close()


def desfazer_fotos(arquivo, quem, progresso):
    cfg = _exigir_nuvemshop()
    if arquivo not in {b["arquivo"] for b in listar_backups_fotos()}:
        raise RuntimeError("Backup não encontrado.")
    with open(os.path.join(backups_dir(), arquivo), encoding="utf-8-sig", newline="") as f:
        linhas = list(csv.DictReader(f))
    feitos, falharam, pulados = 0, [], 0
    for n, l in enumerate(linhas, 1):
        progresso(f"Removendo foto {n} de {len(linhas)}…")
        try:
            _nuvem(cfg, "DELETE", f"/products/{int(l['produto_site'])}/images/{int(l['imagem_site'])}")
            feitos += 1
        except RuntimeError as e:
            if " 404" in str(e):
                pulados += 1                      # já não está mais lá
            else:
                falharam.append(f"{l['nome']}: {e}")
        time.sleep(0.6)
    if not falharam:
        os.replace(os.path.join(backups_dir(), arquivo), os.path.join(backups_dir(), arquivo[:-4] + ".desfeito.csv"))
    con = conectar()
    try:
        progresso("Relendo a loja…")
        sync_nuvemshop(con)
    finally:
        con.close()
    return {"gravados": feitos, "pulados": pulados, "falharam": falharam[:10], "backup": None}


# ------------------------------------------------ publicar produtos na Nuvemshop

MAX_PUBLICAR = 60


def previa_publicar(con, dia=""):
    """Produtos ocultos no site que foram contados em um dia. Não altera nada."""
    base = ("FROM produtos p JOIN canais k ON k.produto_id=p.id AND k.canal='nuvemshop' "
            "WHERE p.estoque IS NOT NULL AND k.status='Oculto'")
    dias = [{"dia": r[0], "n": r[1]} for r in con.execute(
        "SELECT substr(p.conferido_em,1,10) d, COUNT(DISTINCT k.id_externo) " + base +
        " GROUP BY d ORDER BY d DESC LIMIT 14")]
    nvar = {r[0]: r[1] for r in con.execute(
        "SELECT id_externo, COUNT(*) FROM canais WHERE canal='nuvemshop' GROUP BY id_externo")}
    itens, sem_estoque, sem_foto, varias, sem_preco = [], [], [], [], []
    vistos = set()
    if dia:
        for r in con.execute(
                "SELECT p.id pid, p.nome, p.sku, p.estoque, k.preco AS preco, p.foto_url, k.id_externo prod_site " + base +
                " AND substr(p.conferido_em,1,10)=? ORDER BY p.nome", (dia,)):
            if r["prod_site"] in vistos:
                continue
            vistos.add(r["prod_site"])
            if nvar.get(r["prod_site"], 1) > 1:
                varias.append(r["nome"])
            elif not r["preco"] or r["preco"] <= 0:
                sem_preco.append(r["nome"])
            elif r["estoque"] == 0:
                sem_estoque.append(r["nome"])
            elif not r["foto_url"]:
                sem_foto.append(r["nome"])
            else:
                itens.append({"pid": r["pid"], "nome": r["nome"], "sku": r["sku"], "estoque": r["estoque"],
                              "preco": r["preco"], "prod_site": int(r["prod_site"]),
                              "foto": r["foto_url"]})
    return {"dia": dia, "dias": dias, "para_publicar": len(itens), "sem_estoque": len(sem_estoque),
            "sem_foto": len(sem_foto), "varias_variacoes": len(varias), "sem_preco": len(sem_preco),
            "revisar": {"sem_preco": sem_preco[:8], "sem_estoque": sem_estoque[:8], "sem_foto": sem_foto[:8], "varias": varias[:8]},
            "limite": MAX_PUBLICAR, "itens": itens}


def _salvar_backup_publicar(linhas):
    os.makedirs(backups_dir(), exist_ok=True)
    nome = f"publicar_{datetime.now():%Y%m%d_%H%M%S}.csv"
    with open(os.path.join(backups_dir(), nome), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["produto_site", "sku", "nome"])
        w.writerows(linhas)
    return nome


def listar_backups_publicar():
    if not os.path.isdir(backups_dir()):
        return []
    saida = []
    for nome in sorted(os.listdir(backups_dir()), reverse=True):
        if nome.startswith("publicar_") and nome.endswith(".csv") and not nome.endswith(".desfeito.csv"):
            caminho = os.path.join(backups_dir(), nome)
            with open(caminho, encoding="utf-8-sig") as f:
                qtd = max(0, sum(1 for _ in f) - 1)
            saida.append({"arquivo": nome, "itens": qtd, "quando": datetime.fromtimestamp(
                os.path.getmtime(caminho)).strftime("%Y-%m-%d %H:%M")})
    return saida


def _status_site(con, prod_site):
    r = con.execute("SELECT status FROM canais WHERE canal='nuvemshop' AND id_externo=? LIMIT 1",
                    (str(prod_site),)).fetchone()
    return r[0] if r else None


def aplicar_publicar(dia, pids, quem, progresso):
    cfg = _exigir_nuvemshop()
    quer = {int(p) for p in pids}
    if not quer:
        raise RuntimeError("Nada para publicar.")
    if len(quer) > MAX_PUBLICAR:
        raise RuntimeError(f"Por segurança, publique no máximo {MAX_PUBLICAR} por vez.")
    con = conectar()
    try:
        progresso("Lendo a loja para conferir o estado atual…")
        sync_nuvemshop(con)
        plano = {i["pid"]: i for i in previa_publicar(con, dia)["itens"]}
        escolhidos = [plano[p] for p in quer if p in plano]
        pulados = len(quer) - len(escolhidos)
        feitos, falharam = [], []
        for n, i in enumerate(sorted(escolhidos, key=lambda x: x["nome"]), 1):
            progresso(f"Publicando {n} de {len(escolhidos)}: {i['nome'][:40]}")
            try:
                _nuvem(cfg, "PUT", f"/products/{i['prod_site']}", {"published": True})
                feitos.append(i)
            except Exception as e:  # noqa: BLE001
                falharam.append(f"{i['nome']}: {e}")
            time.sleep(0.6)
        backup = _salvar_backup_publicar([[i["prod_site"], i["sku"] or "", i["nome"]] for i in feitos]) if feitos else None
        progresso("Conferindo na loja o que foi publicado…")
        sync_nuvemshop(con)
        ok = 0
        for i in feitos:
            if _status_site(con, i["prod_site"]) == "Publicado":
                ok += 1
                con.execute("INSERT INTO historico(produto_id,quando,campo,de,para,origem) VALUES(?,?,?,?,?,?)",
                            (i["pid"], agora(), "publicado no site", "oculto", "publicado", quem or "painel"))
            else:
                falharam.append(f"{i['nome']}: a loja não mostra como publicado")
        con.commit()
        return {"gravados": ok, "pulados": pulados, "falharam": falharam[:10], "backup": backup}
    finally:
        con.close()


def desfazer_publicar(arquivo, quem, progresso):
    cfg = _exigir_nuvemshop()
    if arquivo not in {b["arquivo"] for b in listar_backups_publicar()}:
        raise RuntimeError("Backup não encontrado.")
    with open(os.path.join(backups_dir(), arquivo), encoding="utf-8-sig", newline="") as f:
        linhas = list(csv.DictReader(f))
    feitos, falharam = 0, []
    for n, l in enumerate(linhas, 1):
        progresso(f"Ocultando {n} de {len(linhas)}…")
        try:
            _nuvem(cfg, "PUT", f"/products/{int(l['produto_site'])}", {"published": False})
            feitos += 1
        except RuntimeError as e:
            falharam.append(f"{l['nome']}: {e}")
        time.sleep(0.6)
    if not falharam:
        os.replace(os.path.join(backups_dir(), arquivo), os.path.join(backups_dir(), arquivo[:-4] + ".desfeito.csv"))
    con = conectar()
    try:
        progresso("Relendo a loja…")
        sync_nuvemshop(con)
    finally:
        con.close()
    return {"gravados": feitos, "pulados": 0, "falharam": falharam[:10], "backup": None}


# ------------------------------------------- cadastrar produtos novos na Nuvemshop

MAX_CADASTRAR = 30


def categorias_nuvemshop_cruas(cfg):
    todas, pagina = [], 1
    while True:
        lote = _nuvem(cfg, "GET", f"/categories?per_page=200&page={pagina}")
        if not isinstance(lote, list) or not lote:
            break
        todas.extend(lote)
        if len(lote) < 200:
            break
        pagina += 1
    return todas


def categorias_lista(con):
    return [{"id": r["id"], "nome": r["nome"]} for r in con.execute(
        "SELECT id, nome FROM categorias_site ORDER BY nome COLLATE NOCASE")]


def _novos_candidatos(con):
    return con.execute(
        "SELECT p.* FROM produtos p WHERE p.origem='manual' AND NOT EXISTS "
        "(SELECT 1 FROM canais k WHERE k.produto_id=p.id AND k.canal='nuvemshop') "
        "ORDER BY p.nome").fetchall()


def salvar_mapa(con, grupo, cat_id, cat_nome):
    grupo = (grupo or "").strip()
    if not grupo:
        raise ValueError("Grupo inválido.")
    if not cat_id:
        con.execute("DELETE FROM categoria_map WHERE grupo=?", (grupo,))
    else:
        con.execute("INSERT OR REPLACE INTO categoria_map VALUES(?,?,?)",
                    (grupo, str(cat_id), (cat_nome or "")[:150]))
    con.commit()
    return {"ok": True}


def _adivinhar_categoria(grupo, cats):
    g = _sem_acento(grupo or "").upper().strip()
    if not g or not cats:
        return None
    ultimos = [(c, _sem_acento(c["nome"].split(" > ")[-1]).upper().strip()) for c in cats]
    for c, n in ultimos:
        if n == g:
            return c["id"]
    for c, n in ultimos:
        if len(n) >= 4 and (n in g or g in n):
            return c["id"]
    return None


def previa_cadastrar(con, cats=None):
    """Produtos cadastrados à mão que ainda não existem no site. Não altera nada.
    Cria mesmo incompleto (fica oculto); o que falta vai marcado para análise."""
    mapa = {r["grupo"]: r["categoria_id"] for r in con.execute("SELECT * FROM categoria_map")}
    nomes_cat = {c["id"]: c["nome"] for c in (cats or [])}
    itens = []
    for r in _novos_candidatos(con):
        ean = "".join(c for c in (r["ean"] or "") if c.isdigit())
        cid = r["categoria_id"] or mapa.get(r["grupo"] or "") or _adivinhar_categoria(r["grupo"], cats)
        faltas = []
        if not r["preco"] or r["preco"] <= 0:
            faltas.append("sem preço")
        if r["estoque"] is None:
            faltas.append("sem contagem")
        if not cid:
            faltas.append("sem categoria")
        if not 6 <= len(ean) <= 14:
            faltas.append("sem código de barras")
        itens.append({"pid": r["id"], "nome": r["nome"], "ean": ean, "preco": r["preco"],
                      "estoque": r["estoque"], "grupo": r["grupo"], "categoria_id": cid,
                      "categoria": nomes_cat.get(cid, ""), "faltas": faltas,
                      "foto": f"/api/foto/{r['id']}" if os.path.exists(os.path.join(fotos_dir(), f"{r['id']}.jpg")) else None})
    return {"para_criar": len(itens), "limite": MAX_CADASTRAR, "itens": itens, "categorias": cats}


def _salvar_backup_cadastrar(linhas):
    os.makedirs(backups_dir(), exist_ok=True)
    nome = f"cadastrar_{datetime.now():%Y%m%d_%H%M%S}.csv"
    with open(os.path.join(backups_dir(), nome), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["produto_site", "nome", "ean"])
        w.writerows(linhas)
    return nome


def listar_backups_cadastrar():
    if not os.path.isdir(backups_dir()):
        return []
    saida = []
    for nome in sorted(os.listdir(backups_dir()), reverse=True):
        if nome.startswith("cadastrar_") and nome.endswith(".csv") and not nome.endswith(".desfeito.csv"):
            caminho = os.path.join(backups_dir(), nome)
            with open(caminho, encoding="utf-8-sig") as f:
                qtd = max(0, sum(1 for _ in f) - 1)
            saida.append({"arquivo": nome, "itens": qtd, "quando": datetime.fromtimestamp(
                os.path.getmtime(caminho)).strftime("%Y-%m-%d %H:%M")})
    return saida


def aplicar_cadastrar(itens_in, quem, progresso):
    cfg = _exigir_nuvemshop()
    cat_de = {int(i["pid"]): (str(i.get("categoria_id") or "") or None) for i in itens_in}
    quer = set(cat_de)
    if not quer:
        raise RuntimeError("Nada para cadastrar.")
    if len(quer) > MAX_CADASTRAR:
        raise RuntimeError(f"Por segurança, cadastre no máximo {MAX_CADASTRAR} por vez.")
    con = conectar()
    try:
        progresso("Lendo a loja para não duplicar nenhum produto…")
        sync_nuvemshop(con)                     # quem já existe no site (mesmo código de barras) sai do plano
        plano = {i["pid"]: i for i in previa_cadastrar(con)["itens"]}
        escolhidos = [plano[p] | {"categoria_id": cat_de[p]} for p in quer if p in plano]
        pulados = len(quer) - len(escolhidos)
        criados, falharam = [], []
        for n, i in enumerate(sorted(escolhidos, key=lambda x: x["nome"]), 1):
            progresso(f"Cadastrando {n} de {len(escolhidos)}: {i['nome'][:40]}")
            try:
                variante = {"price": f"{(i['preco'] or 0):.2f}"}
                if i["estoque"] is not None:
                    variante.update(stock_management=True, stock=int(i["estoque"]))
                if 6 <= len(i["ean"]) <= 14:
                    variante["barcode"] = i["ean"]
                else:
                    variante["sku"] = f"PAINEL-{i['pid']}"        # para o painel reconhecer o produto depois
                corpo = {"name": {"pt": i["nome"]}, "published": False, "variants": [variante]}
                if i["categoria_id"]:
                    corpo["categories"] = [int(i["categoria_id"])]
                r = _nuvem(cfg, "POST", "/products", corpo)
                if not r.get("id"):
                    raise RuntimeError("a loja não devolveu o número do produto")
                if i["categoria_id"]:
                    con.execute("UPDATE produtos SET categoria_id=?, categoria_nome=(SELECT nome FROM categorias_site "
                                "WHERE id=?) WHERE id=?", (i["categoria_id"], i["categoria_id"], i["pid"]))
                    con.commit()
                if "sku" in variante:
                    con.execute("UPDATE produtos SET sku=? WHERE id=?", (variante["sku"], i["pid"]))
                    con.commit()
                criados.append(i | {"prod_site": r["id"]})
            except Exception as e:  # noqa: BLE001
                falharam.append(f"{i['nome']}: {e}")
            time.sleep(0.6)
        backup = _salvar_backup_cadastrar([[i["prod_site"], i["nome"], i["ean"]] for i in criados]) if criados else None
        for i in criados:                       # foto, se houver
            arq = os.path.join(fotos_dir(), f"{i['pid']}.jpg")
            if os.path.exists(arq):
                try:
                    with open(arq, "rb") as f:
                        b64 = base64.b64encode(f.read()).decode()
                    _nuvem(cfg, "POST", f"/products/{i['prod_site']}/images",
                           {"filename": f"produto-{i['pid']}.jpg", "position": 1, "attachment": b64})
                except Exception as e:  # noqa: BLE001
                    falharam.append(f"{i['nome']}: criado, mas a foto não subiu ({e})")
                time.sleep(0.6)
        progresso("Conferindo na loja o que foi criado…")
        sync_nuvemshop(con)
        ok = 0
        for i in criados:
            if con.execute("SELECT 1 FROM canais WHERE produto_id=? AND canal='nuvemshop'", (i["pid"],)).fetchone():
                ok += 1
                con.execute("INSERT INTO historico(produto_id,quando,campo,de,para,origem) VALUES(?,?,?,?,?,?)",
                            (i["pid"], agora(), "cadastro no site", "", "criado oculto na Nuvemshop", quem or "painel"))
            else:
                falharam.append(f"{i['nome']}: criado, mas a leitura da loja não o achou")
        con.commit()
        return {"gravados": ok, "pulados": pulados, "falharam": falharam[:10], "backup": backup}
    finally:
        con.close()


def desfazer_cadastrar(arquivo, quem, progresso):
    """Apaga da loja só os produtos que esse envio criou, e só se ainda estiverem ocultos."""
    cfg = _exigir_nuvemshop()
    if arquivo not in {b["arquivo"] for b in listar_backups_cadastrar()}:
        raise RuntimeError("Backup não encontrado.")
    with open(os.path.join(backups_dir(), arquivo), encoding="utf-8-sig", newline="") as f:
        linhas = list(csv.DictReader(f))
    feitos, pulados, falharam = 0, 0, []
    for n, l in enumerate(linhas, 1):
        progresso(f"Apagando {n} de {len(linhas)}…")
        try:
            atual = _nuvem(cfg, "GET", f"/products/{int(l['produto_site'])}")
            if atual.get("published"):
                pulados += 1
                falharam.append(f"{l['nome']}: já está publicado; oculte antes de apagar")
            else:
                _nuvem(cfg, "DELETE", f"/products/{int(l['produto_site'])}")
                feitos += 1
        except RuntimeError as e:
            if " 404" in str(e):
                pulados += 1
            else:
                falharam.append(f"{l['nome']}: {e}")
        time.sleep(0.6)
    if not falharam:
        os.replace(os.path.join(backups_dir(), arquivo), os.path.join(backups_dir(), arquivo[:-4] + ".desfeito.csv"))
    con = conectar()
    try:
        progresso("Relendo a loja…")
        sync_nuvemshop(con)
    finally:
        con.close()
    return {"gravados": feitos, "pulados": pulados, "falharam": falharam[:10], "backup": None}


# ------------------------------------------------------------------- consultas

FILTROS = {
    "todos": "1=1",
    "nao_conferido": "p.estoque IS NULL",
    "conferido": "p.estoque IS NOT NULL",
    "zerado": "p.estoque = 0",
    "vende_sem_estoque": "p.estoque = 0 AND EXISTS (SELECT 1 FROM canais c WHERE "
                         "c.produto_id=p.id AND c.vendendo=1)",
    "parado_com_estoque": "p.estoque > 0 AND EXISTS (SELECT 1 FROM canais c WHERE "
                          "c.produto_id=p.id AND c.vendendo=0)",
    "sem_gestorz": "p.origem <> 'gestorz'",
    "sem_ean": "(p.ean_norm IS NULL OR p.ean_norm = '' OR p.ean_norm LIKE '200%')",
    "em_nenhum_canal": "NOT EXISTS (SELECT 1 FROM canais c WHERE c.produto_id=p.id)",
    "sem_foto": "(p.foto_url IS NULL OR p.foto_url = '') AND NOT EXISTS "
                "(SELECT 1 FROM fotos f WHERE f.produto_id=p.id)",
    "foto_tirada": "EXISTS (SELECT 1 FROM fotos f WHERE f.produto_id=p.id)",
    "cadastro_manual": "p.origem = 'manual'",
}
FILTROS["est_negativo"] = "p.estimado IS NOT NULL AND p.estimado < 0"
FILTROS["est_baixo"] = "p.estimado IS NOT NULL AND p.estimado >= 0 AND p.estimado <= 3"
FILTROS["tem_estimado"] = "p.estimado IS NOT NULL"
for _c in CANAIS:
    FILTROS["no_" + _c] = f"EXISTS (SELECT 1 FROM canais c WHERE c.produto_id=p.id AND c.canal='{_c}')"
    FILTROS["fora_" + _c] = (f"NOT EXISTS (SELECT 1 FROM canais c WHERE c.produto_id=p.id "
                             f"AND c.canal='{_c}')")
    FILTROS["vendendo_" + _c] = (f"EXISTS (SELECT 1 FROM canais c WHERE c.produto_id=p.id "
                                 f"AND c.canal='{_c}' AND c.vendendo=1)")


def _where(q, filtro, grupo):
    conds, args = [FILTROS.get(filtro, "1=1")], []
    for termo in (q or "").split():
        conds.append("(p.nome LIKE ? OR p.ean LIKE ? OR p.sku LIKE ? OR p.gz_codigo = ?)")
        args += [f"%{termo}%", f"%{termo}%", f"%{termo}%", termo]
    if grupo == "cat:__sem":
        conds.append("p.categoria_id IS NULL")
    elif grupo.startswith("cat:"):
        conds.append("p.categoria_id = ?")
        args.append(grupo[4:])
    elif grupo:
        conds.append("p.grupo = ?")
        args.append(grupo)
    return " AND ".join(conds), args


def _canais_de(con, ids):
    saida = {i: [] for i in ids}
    if not ids:
        return saida
    marcas = ",".join("?" * len(ids))
    for c in con.execute(f"SELECT * FROM canais WHERE produto_id IN ({marcas}) "
                         "ORDER BY canal, nome", ids):
        saida[c["produto_id"]].append(dict(c))
    return saida


def _anexar_fotos(con, itens):
    """Acrescenta o endereço da foto: a tirada na prateleira vale mais que a do site."""
    ids = [i["id"] for i in itens]
    locais = {}
    if ids:
        marcas = ",".join("?" * len(ids))
        locais = {r["produto_id"]: r["quando"] for r in con.execute(
            f"SELECT produto_id, quando FROM fotos WHERE produto_id IN ({marcas})", ids)}
    for i in itens:
        q = locais.get(i["id"])
        i["foto_local"] = bool(q)
        i["foto"] = (f"/api/foto/{i['id']}?v=" + "".join(c for c in q if c.isdigit())) if q \
            else (i.get("foto_url") or None)


def listar(con, q="", filtro="todos", grupo="", pagina=1, por_pagina=50):
    where, args = _where(q, filtro, grupo)
    total = con.execute(f"SELECT COUNT(*) FROM produtos p WHERE {where}", args).fetchone()[0]
    linhas = [dict(r) for r in con.execute(
        f"SELECT p.* FROM produtos p WHERE {where} ORDER BY p.nome COLLATE NOCASE "
        "LIMIT ? OFFSET ?", args + [por_pagina, (pagina - 1) * por_pagina])]
    canais = _canais_de(con, [l["id"] for l in linhas])
    for l in linhas:
        l["canais"] = canais[l["id"]]
    _anexar_fotos(con, linhas)
    return {"total": total, "pagina": pagina, "por_pagina": por_pagina, "itens": linhas}


def resumo(con):
    um = lambda sql: con.execute(sql).fetchone()[0]
    r = {"total": um("SELECT COUNT(*) FROM produtos"),
         "filtros": {k: um(f"SELECT COUNT(*) FROM produtos p WHERE {v}")
                     for k, v in FILTROS.items()},
         "grupos": [x[0] for x in con.execute(
             "SELECT DISTINCT grupo FROM produtos WHERE grupo<>'' ORDER BY grupo")],
         "grupos_n": [{"grupo": x[0], "n": x[1]} for x in con.execute(
             "SELECT grupo, COUNT(*) FROM produtos WHERE grupo<>'' GROUP BY grupo "
             "ORDER BY COUNT(*) DESC, grupo")],
         "categorias_n": ([{"key": "cat:" + x[0], "nome": x[1], "n": x[2]} for x in con.execute(
             "SELECT c.id, c.nome, COUNT(p.id) FROM categorias_site c LEFT JOIN produtos p ON p.categoria_id=c.id "
             "GROUP BY c.id ORDER BY c.nome COLLATE NOCASE")] +
             [{"key": "cat:__sem", "nome": "Sem categoria", "n": um("SELECT COUNT(*) FROM produtos WHERE categoria_id IS NULL")}]
             ) if um("SELECT COUNT(*) FROM categorias_site") else [],
         "meta": {x["chave"]: x["valor"] for x in con.execute("SELECT * FROM meta")},
         "fotos_tiradas": um("SELECT COUNT(*) FROM fotos"),
         "importacoes": {"vendas": um("SELECT COUNT(*) FROM importacoes WHERE tipo='vendas'"),
                         "notas": um("SELECT COUNT(*) FROM importacoes WHERE tipo='notas'"),
                         "entradas_pendentes": um(
                             "SELECT COUNT(*) FROM (SELECT 1 FROM notas_itens n LEFT JOIN de_para d "
                             "ON d.cnpj=n.cnpj AND d.cod_fornecedor=n.cod_fornecedor "
                             "WHERE d.estado IS NULL GROUP BY n.cnpj, n.cod_fornecedor)"),
                         "ultima_venda": (con.execute("SELECT MAX(fim) FROM importacoes "
                                                      "WHERE tipo='vendas'").fetchone()[0])},
         "servidor": VERSAO_SERVIDOR,
         "pasta": BASE,
         "nuvemshop_configurada": bool(carregar_config()["token"])}
    return r


def detalhe(con, pid):
    p = con.execute("SELECT * FROM produtos WHERE id=?", (pid,)).fetchone()
    if not p:
        return None
    d = dict(p)
    d["canais"] = _canais_de(con, [pid])[pid]
    _anexar_fotos(con, [d])
    d["movimento"] = movimento_produto(con, pid)
    d["historico"] = [dict(h) for h in con.execute(
        "SELECT * FROM historico WHERE produto_id=? ORDER BY id DESC LIMIT 50", (pid,))]
    return d


def alterar(con, pid, dados):
    """Altera estoque oficial e/ou observação. Fica só no banco local."""
    p = con.execute("SELECT * FROM produtos WHERE id=?", (pid,)).fetchone()
    if not p:
        raise ValueError("Produto não encontrado.")
    quem = (str(dados.get("quem") or "").strip() or "painel")[:40]
    if "estoque" in dados:
        novo = dados["estoque"]
        if novo in ("", None):
            novo = None
        else:
            novo = int(novo)
            if novo < 0:
                raise ValueError("O estoque não pode ser negativo.")
        con.execute("UPDATE produtos SET estoque=?, conferido_em=?, atualizado_em=? WHERE id=?",
                    (novo, agora() if novo is not None else None, agora(), pid))
        con.execute("INSERT INTO historico(produto_id,quando,campo,de,para,origem) "
                    "VALUES(?,?,?,?,?,?)",
                    (pid, agora(), "estoque",
                     "" if p["estoque"] is None else str(p["estoque"]),
                     "" if novo is None else str(novo), quem))
    mudar = {}
    if "nome" in dados:
        nome = " ".join(str(dados["nome"] or "").split())
        if not 2 <= len(nome) <= 150:
            raise ValueError("O nome precisa ter de 2 a 150 letras.")
        mudar["nome"] = nome
    for campo in ("preco", "custo"):
        if campo in dados:
            v = None if str(dados[campo] if dados[campo] is not None else "").strip() == "" else num(str(dados[campo]).replace(",", "."))
            if v is not None and not 0 <= v <= 100000:
                raise ValueError("Valor fora do normal.")
            mudar[campo] = v
    if "ean" in dados:
        ean = "".join(c for c in str(dados["ean"] or "") if c.isdigit())
        if ean and not 6 <= len(ean) <= 14:
            raise ValueError("Código de barras inválido (6 a 14 números).")
        if ean != (p["ean"] or "") and not p["sku"] and con.execute(
                "SELECT 1 FROM canais WHERE produto_id=? AND canal='nuvemshop'", (pid,)).fetchone():
            raise ValueError("Este produto está no site ligado pelo código de barras. Troque o código "
                             "na Nuvemshop primeiro e depois use “Ler a Nuvemshop agora”.")
        if ean:
            outro = con.execute("SELECT nome FROM produtos WHERE ean_norm=? AND id<>? LIMIT 1", (norm_ean(ean), pid)).fetchone()
            if outro:
                raise ValueError(f"Este código já é de: {outro['nome']}.")
        mudar["ean"] = ean
    if "categoria_id" in dados:
        cid = str(dados["categoria_id"] or "")
        cat = con.execute("SELECT id, nome FROM categorias_site WHERE id=?", (cid,)).fetchone() if cid else None
        if cid and not cat:
            raise ValueError("Categoria desconhecida. Leia a Nuvemshop de novo.")
        mudar["categoria_id"] = cat["id"] if cat else None
        mudar["categoria_nome"] = cat["nome"] if cat else None
    for campo, novo in mudar.items():
        antigo = p[campo]
        if (antigo or None) == (novo or None):
            continue
        con.execute(f"UPDATE produtos SET {campo}=?, atualizado_em=? WHERE id=?", (novo, agora(), pid))
        if campo == "ean":
            con.execute("UPDATE produtos SET ean_norm=? WHERE id=?", (norm_ean(novo), pid))
        if campo != "categoria_nome":
            con.execute("INSERT INTO historico(produto_id,quando,campo,de,para,origem) VALUES(?,?,?,?,?,?)",
                        (pid, agora(), campo, "" if antigo is None else str(antigo), "" if novo is None else str(novo), quem))
    if "obs" in dados:
        obs = (dados["obs"] or "").strip()
        if obs != (p["obs"] or ""):
            con.execute("UPDATE produtos SET obs=?, atualizado_em=? WHERE id=?",
                        (obs, agora(), pid))
            con.execute("INSERT INTO historico(produto_id,quando,campo,de,para,origem) "
                        "VALUES(?,?,?,?,?,?)", (pid, agora(), "obs", p["obs"] or "", obs, quem))
    con.commit()
    recalcular(con, [pid])
    return detalhe(con, pid)


# ------------------------------------------------- vendas, notas e estoque estimado

def enviar_produto_site(con, pid, quem, campos=()):
    """Manda para a Nuvemshop só os campos que você acabou de editar (nome, categoria, preço)."""
    campos = {c for c in campos if c in ("nome", "categoria", "preco")}
    if not campos:
        raise ValueError("Nada para enviar: mude o nome, a categoria ou o preço primeiro.")
    cfg = _exigir_nuvemshop()
    p = con.execute("SELECT * FROM produtos WHERE id=?", (pid,)).fetchone()
    if not p:
        raise ValueError("Produto não encontrado.")
    ks = con.execute("SELECT id_externo, id_variacao FROM canais WHERE produto_id=? AND canal='nuvemshop'", (pid,)).fetchall()
    if not ks:
        raise ValueError("Este produto ainda não está no site. Use “Cadastrar no site”.")
    prod_site = ks[0]["id_externo"]
    n = con.execute("SELECT COUNT(*) FROM canais WHERE canal='nuvemshop' AND id_externo=?", (prod_site,)).fetchone()[0]
    if len(ks) > 1 or n > 1:
        raise ValueError("Este produto tem várias variações no site. Edite direto na Nuvemshop.")
    corpo = {}
    if "nome" in campos:
        corpo["name"] = {"pt": p["nome"]}
    if "categoria" in campos and p["categoria_id"]:
        corpo["categories"] = [int(p["categoria_id"])]
    if corpo:
        _nuvem(cfg, "PUT", f"/products/{prod_site}", corpo)
    if "preco" in campos and p["preco"]:
        _nuvem(cfg, "PUT", f"/products/{prod_site}/variants/{ks[0]['id_variacao']}", {"price": f"{p['preco']:.2f}"})
    if "nome" in campos:
        con.execute("UPDATE canais SET nome=? WHERE produto_id=? AND canal='nuvemshop'", (p["nome"], pid))
    if "preco" in campos and p["preco"]:
        con.execute("UPDATE canais SET preco=? WHERE produto_id=? AND canal='nuvemshop'", (p["preco"], pid))
    con.execute("INSERT INTO historico(produto_id,quando,campo,de,para,origem) VALUES(?,?,?,?,?,?)",
                (pid, agora(), "dados no site", "", "enviados: " + ", ".join(sorted(campos)), quem or "painel"))
    con.commit()
    return detalhe(con, pid)


def _sem_acento(t):
    t = unicodedata.normalize("NFD", str(t or ""))
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def _cab(t):
    return re.sub(r"[^A-Z0-9]+", " ", _sem_acento(t).upper()).strip()


def num_br(t):
    """Número de planilha brasileira ou americana: 1.234,56 | 1234,56 | 1234.56."""
    t = str(t or "").strip().replace("R$", "").replace(" ", "")
    if not t:
        return 0.0
    if "," in t and "." in t:
        if t.rfind(",") > t.rfind("."):
            t = t.replace(".", "").replace(",", ".")
        else:
            t = t.replace(",", "")
    elif "," in t:
        t = t.replace(",", ".")
    return float(t)


def _ler_csv(raw):
    texto = raw.decode("utf-8-sig", errors="replace") if isinstance(raw, bytes) else raw
    primeira = texto.split("\n", 1)[0]
    delim = ";" if primeira.count(";") > primeira.count(",") else ","
    leitor = csv.reader(io.StringIO(texto), delimiter=delim)
    linhas = [l for l in leitor if any(c.strip() for c in l)]
    if len(linhas) < 2:
        raise ValueError("O arquivo está vazio ou sem a linha de títulos.")
    return [_cab(c) for c in linhas[0]], linhas[1:]


def _data(t):
    t = str(t or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(t[:19] if "%H" in fmt else t[:10], fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError(f"Data inválida: {t!r}. Use dia/mês/ano.")


def _col(cab, *opcoes, exato=False):
    for o in opcoes:
        for i, c in enumerate(cab):
            if c == o or (not exato and c.startswith(o)):
                return i
    return None


def importar_vendas(con, raw, inicio, fim, arquivo="", quem="painel"):
    """Vendas do caixa (Gestor Z) de um período. Períodos não podem se sobrepor,
    senão a mesma venda seria descontada duas vezes; o mesmo período é substituído."""
    inicio, fim = _data(inicio), _data(fim)
    if fim < inicio:
        raise ValueError("A data final é antes da inicial.")
    cab, linhas = _ler_csv(raw)
    ic = _col(cab, "CODIGO INTERNO", "CODIGO", "COD")
    inm = _col(cab, "NOME DO ITEM", "NOME", "DESCRICAO", "PRODUTO")
    iq = _col(cab, "QTDE VENDA", "QUANTIDADE VENDA", "QTD VENDA", exato=True)
    iu = _col(cab, "UND VENDA", "UNIDADE", "UND")
    iv = _col(cab, "VALOR VENDA TOTAL", "VALOR")
    if ic is None or iq is None:
        raise ValueError("Não achei as colunas CÓDIGO INTERNO e QTDE VENDA. "
                         "É o relatório de vendas por item do Gestor Z?")
    for r in con.execute("SELECT * FROM importacoes WHERE tipo='vendas'"):
        if (r["inicio"], r["fim"]) == (inicio, fim):
            _remover_importacao(con, r["id"])
        elif not (fim < r["inicio"] or inicio > r["fim"]):
            raise ValueError(
                f"Esse período se sobrepõe a vendas já importadas ({r['inicio']} a {r['fim']}). "
                "Remova a importação antiga na lista abaixo ou use datas que não se cruzem.")
    cur = con.execute("INSERT INTO importacoes(tipo,inicio,fim,arquivo,quando,quem) "
                      "VALUES('vendas',?,?,?,?,?)", (inicio, fim, arquivo[:120], agora(), quem))
    imp = cur.lastrowid
    n = 0
    for l in linhas:
        if len(l) <= max(ic, iq):
            continue
        cod = l[ic].strip()
        if not cod:
            continue
        try:
            qtd = num_br(l[iq])
            valor = num_br(l[iv]) if iv is not None and len(l) > iv else 0.0
        except ValueError:
            continue
        con.execute("INSERT INTO vendas_gz(imp_id,inicio,fim,gz_codigo,nome,qtd,unidade,valor) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (imp, inicio, fim, cod, l[inm].strip() if inm is not None else "", qtd,
                     (l[iu].strip().upper() if iu is not None and len(l) > iu else ""), valor))
        n += 1
    con.execute("UPDATE importacoes SET linhas=?, novas=? WHERE id=?", (n, n, imp))
    con.commit()
    recalcular(con)
    return {"ok": True, "linhas": n, "inicio": inicio, "fim": fim}


NOTA_COLS = {"emissao": ("EMISSAO", "DATA"), "fornecedor": ("FORNECEDOR", "EMITENTE"),
             "cnpj": ("CNPJ",), "nota": ("NOTA", "NUMERO"), "serie": ("SERIE",),
             "cod": ("COD FORNECEDOR", "CODIGO FORNECEDOR", "COD"), "descricao": ("DESCRICAO", "PRODUTO"),
             "un": ("UN", "UNIDADE"), "quantidade": ("QUANTIDADE", "QTD", "QTDE"),
             "cfop": ("CFOP",), "ncm": ("NCM",)}


def importar_notas(con, raw, arquivo="", quem="painel"):
    """Itens das notas de entrada. Reimportar o mesmo arquivo não duplica nada."""
    cab, linhas = _ler_csv(raw)
    ix = {k: _col(cab, *v, exato=True) for k, v in NOTA_COLS.items()}
    if None in (ix["emissao"], ix["cnpj"], ix["nota"], ix["cod"], ix["quantidade"]):
        raise ValueError("Não achei as colunas emissao, cnpj, nota, cod_fornecedor e quantidade. "
                         "É o arquivo de itens das notas de entrada?")
    cur = con.execute("INSERT INTO importacoes(tipo,arquivo,quando,quem) VALUES('notas',?,?,?)",
                      (arquivo[:120], agora(), quem))
    imp = cur.lastrowid
    vistos = collections.Counter()
    novas = total = 0
    datas = []
    for l in linhas:
        g = lambda k: (l[ix[k]].strip() if ix[k] is not None and len(l) > ix[k] else "")
        try:
            emissao, qtd = _data(g("emissao")), num_br(g("quantidade"))
        except ValueError:
            continue
        cnpj = re.sub(r"\D", "", g("cnpj")) or g("fornecedor")[:30]
        base = "|".join([cnpj, g("nota"), g("serie"), g("cod"), g("descricao"), g("un"), repr(qtd)])
        vistos[base] += 1                       # mesma linha repetida na mesma nota conta de novo
        chave = hashlib.sha1(f"{base}|{vistos[base]}".encode()).hexdigest()
        total += 1
        datas.append(emissao)
        c = con.execute(
            "INSERT OR IGNORE INTO notas_itens(chave,imp_id,emissao,fornecedor,cnpj,nota,serie,"
            "cod_fornecedor,descricao,un,quantidade,cfop,ncm) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (chave, imp, emissao, g("fornecedor"), cnpj, g("nota"), g("serie"), g("cod"),
             g("descricao"), g("un"), qtd, g("cfop"), g("ncm")))
        novas += c.rowcount
    con.execute("UPDATE importacoes SET linhas=?, novas=?, inicio=?, fim=? WHERE id=?",
                (total, novas, min(datas) if datas else None, max(datas) if datas else None, imp))
    if novas == 0:                          # nada de novo: não deixa registro vazio na lista
        con.execute("DELETE FROM importacoes WHERE id=?", (imp,))
    con.commit()
    recalcular(con)
    return {"ok": True, "linhas": total, "novas": novas, "repetidas": total - novas}


def _remover_importacao(con, imp_id):
    con.execute("DELETE FROM vendas_gz WHERE imp_id=?", (imp_id,))
    con.execute("DELETE FROM notas_itens WHERE imp_id=?", (imp_id,))
    con.execute("DELETE FROM importacoes WHERE id=?", (imp_id,))


def remover_importacao(con, imp_id):
    _remover_importacao(con, int(imp_id))
    con.commit()
    recalcular(con)
    return {"ok": True}


def listar_importacoes(con):
    return {"itens": [dict(r) for r in con.execute(
        "SELECT * FROM importacoes ORDER BY id DESC LIMIT 60")]}


def _fator_sugerido(un):
    m = re.search(r"(\d+)", str(un or ""))
    v = int(m.group(1)) if m else 1
    return v if v > 0 else 1


def recalcular(con, ids=None):
    """estimado = contagem + entradas (notas depois da contagem x unidades por caixa)
    - vendas (períodos que começam depois da contagem). Só para quem tem contagem."""
    where, args = "", []
    if ids is not None:
        ids = list(ids)
        if not ids:
            return
        where = " AND p.id IN (%s)" % ",".join("?" * len(ids))
        args = ids
    prods = con.execute("SELECT p.id, p.estoque, p.conferido_em, p.gz_codigo FROM produtos p "
                        "WHERE 1=1" + where, args).fetchall()
    ent = {}
    for r in con.execute(
            "SELECT d.produto_id AS pid, n.emissao AS em, SUM(n.quantidade*d.fator) AS q "
            "FROM notas_itens n JOIN de_para d ON d.cnpj=n.cnpj AND d.cod_fornecedor=n.cod_fornecedor "
            "WHERE d.estado='confirmado' AND d.produto_id IS NOT NULL GROUP BY d.produto_id, n.emissao"):
        ent.setdefault(r["pid"], []).append((r["em"], r["q"] or 0))
    ven = {}
    for r in con.execute("SELECT gz_codigo, inicio, unidade, SUM(qtd) AS q FROM vendas_gz "
                         "GROUP BY gz_codigo, inicio, unidade"):
        ven.setdefault(r["gz_codigo"], []).append((r["inicio"], r["unidade"], r["q"] or 0))
    for p in prods:
        est, aviso = None, None
        if p["estoque"] is not None and p["conferido_em"]:
            dia = p["conferido_em"][:10]
            e = sum(q for em, q in ent.get(p["id"], []) if em > dia)
            v = 0.0
            for ini, un, q in ven.get(str(p["gz_codigo"] or ""), []):
                if ini > dia:
                    if un == "KG":
                        aviso = "peso"
                    v += q
            if aviso != "peso":
                est = round(p["estoque"] + e - v, 2)
        con.execute("UPDATE produtos SET estimado=?, est_aviso=? WHERE id=?", (est, aviso, p["id"]))
    con.commit()


def movimento_produto(con, pid):
    """O que entrou e saiu desde a contagem, para mostrar na ficha do produto."""
    p = con.execute("SELECT estoque, conferido_em, gz_codigo FROM produtos WHERE id=?",
                    (pid,)).fetchone()
    if not p or p["estoque"] is None or not p["conferido_em"]:
        return None
    dia = p["conferido_em"][:10]
    entradas = [dict(r) for r in con.execute(
        "SELECT n.emissao, n.fornecedor, n.nota, n.quantidade, n.un, d.fator, "
        "n.quantidade*d.fator AS unidades FROM notas_itens n JOIN de_para d "
        "ON d.cnpj=n.cnpj AND d.cod_fornecedor=n.cod_fornecedor "
        "WHERE d.estado='confirmado' AND d.produto_id=? AND n.emissao>? ORDER BY n.emissao DESC",
        (pid, dia))]
    vendas = [dict(r) for r in con.execute(
        "SELECT inicio, fim, qtd, unidade FROM vendas_gz WHERE gz_codigo=? AND inicio>? "
        "ORDER BY inicio DESC", (str(p["gz_codigo"] or ""), dia))]
    return {"contagem_em": dia, "entradas": entradas, "vendas": vendas}


_STOP = {"DE", "DA", "DO", "COM", "PARA", "C", "E", "EM", "UN", "CX", "PCT", "KG", "G", "ML", "L"}


def _tokens(t):
    return {w for w in re.findall(r"[A-Z0-9]{2,}", _sem_acento(t).upper()) if w not in _STOP}


_INDICE = {"n": -1, "tok": {}, "lista": []}


def _indice_produtos(con):
    n = tuple(con.execute("SELECT COUNT(*), MAX(atualizado_em) FROM produtos").fetchone())
    if _INDICE["n"] != n:
        lista = [(r["id"], r["nome"]) for r in con.execute(
            "SELECT id, nome FROM produtos WHERE ativo=1 OR ativo IS NULL")]
        tok = collections.defaultdict(list)
        for pid, nome in lista:
            for w in _tokens(nome):
                tok[w].append(pid)
        _INDICE.update(n=n, tok=tok, lista={pid: nome for pid, nome in lista})
    return _INDICE


def sugerir_produtos(con, descricao, limite=3):
    ind = _indice_produtos(con)
    pontos = collections.Counter()
    for w in _tokens(descricao):
        ids = ind["tok"].get(w, [])
        if 0 < len(ids) <= 400:
            for i in ids:
                pontos[i] += 1.0 / (1 + len(ids) / 50)
    ref = _sem_acento(descricao).upper()
    saida = []
    for pid, _ in pontos.most_common(25):
        nome = ind["lista"][pid]
        r = difflib.SequenceMatcher(None, ref, _sem_acento(nome).upper()).ratio()
        saida.append((r, pid, nome))
    saida.sort(reverse=True)
    return [{"id": pid, "nome": nome, "parecido": round(r, 2)} for r, pid, nome in saida[:limite]]


_SUG_CACHE = {}


def _sug_cache(con, descricao):
    ind = _indice_produtos(con)
    chave = (ind["n"], descricao)
    if chave not in _SUG_CACHE:
        if len(_SUG_CACHE) > 5000:
            _SUG_CACHE.clear()
        _SUG_CACHE[chave] = sugerir_produtos(con, descricao)
    return _SUG_CACHE[chave]


def listar_entradas(con, estado="pendente", q="", pagina=1, por_pagina=25, so_contados=False):
    """Itens das notas agrupados por fornecedor+código, com o vínculo (se houver)."""
    conds, args = ["1=1"], []
    for t in (q or "").split():
        conds.append("(n.descricao LIKE ? OR n.fornecedor LIKE ? OR n.cod_fornecedor LIKE ?)")
        args += [f"%{t}%"] * 3
    filtro_estado = {"pendente": "d.estado IS NULL", "confirmado": "d.estado='confirmado'",
                     "ignorar": "d.estado='ignorar'"}.get(estado, "1=1")
    base = (" FROM notas_itens n LEFT JOIN de_para d ON d.cnpj=n.cnpj AND "
            "d.cod_fornecedor=n.cod_fornecedor WHERE " + " AND ".join(conds) +
            " AND " + filtro_estado + " GROUP BY n.cnpj, n.cod_fornecedor")
    linhas = con.execute(
        "SELECT n.cnpj, n.cod_fornecedor AS cod, MAX(n.descricao) AS descricao, "
        "MAX(n.fornecedor) AS fornecedor, MAX(n.un) AS un, SUM(n.quantidade) AS quantidade, "
        "COUNT(DISTINCT n.nota) AS notas, MAX(n.emissao) AS ultima, d.estado, d.fator, "
        "d.produto_id, (SELECT nome FROM produtos WHERE id=d.produto_id) AS produto, "
        "(SELECT estoque FROM produtos WHERE id=d.produto_id) AS prod_estoque" + base +
        " ORDER BY MAX(n.emissao) DESC, n.descricao", args).fetchall()
    contados = {r[0] for r in con.execute("SELECT id FROM produtos WHERE estoque IS NOT NULL")}
    itens = []
    for r in linhas:
        d = dict(r)
        d["fator_sugerido"] = _fator_sugerido(d["un"])
        if d["estado"] is None:
            sug = [dict(x) for x in _sug_cache(con, d["descricao"])]
            for x in sug:
                x["contado"] = x["id"] in contados
            d["sugestoes"] = sug
            d["rapido"] = bool(sug and sug[0]["parecido"] >= 0.9)
            d["contado"] = any(x["contado"] for x in sug)
        elif d["estado"] == "confirmado":
            d["contado"] = d.pop("prod_estoque", None) is not None
        if so_contados and d.get("estado") != "ignorar" and not d.get("contado"):
            continue
        d.pop("prod_estoque", None)
        itens.append(d)
    total = len(itens)
    itens = itens[(pagina - 1) * por_pagina: pagina * por_pagina]
    contagem = {k: con.execute(
        "SELECT COUNT(*) FROM (SELECT 1 FROM notas_itens n LEFT JOIN de_para d ON d.cnpj=n.cnpj "
        "AND d.cod_fornecedor=n.cod_fornecedor WHERE " + w + " GROUP BY n.cnpj, n.cod_fornecedor)"
    ).fetchone()[0] for k, w in (("pendente", "d.estado IS NULL"),
                                 ("confirmado", "d.estado='confirmado'"),
                                 ("ignorar", "d.estado='ignorar'"))}
    return {"total": total, "pagina": pagina, "por_pagina": por_pagina, "itens": itens,
            "contagem": contagem}


def ignorar_lote(con, itens, quem="painel"):
    n = 0
    for i in itens[:500]:
        cnpj, cod = str(i.get("cnpj") or ""), str(i.get("cod") or "")
        if cnpj and cod:
            antigo = con.execute("SELECT produto_id FROM de_para WHERE cnpj=? AND cod_fornecedor=?",
                                 (cnpj, cod)).fetchone()
            con.execute("INSERT OR REPLACE INTO de_para VALUES(?,?,NULL,NULL,'ignorar',?,?)",
                        (cnpj, cod, quem, agora()))
            n += 1
    con.commit()
    recalcular(con)
    return {"ok": True, "ignorados": n}


def vincular_entrada(con, d, quem="painel"):
    cnpj, cod = str(d.get("cnpj") or ""), str(d.get("cod") or "")
    if not cnpj or not cod:
        raise ValueError("Item sem fornecedor/código.")
    antigo = con.execute("SELECT produto_id FROM de_para WHERE cnpj=? AND cod_fornecedor=?",
                         (cnpj, cod)).fetchone()
    afetados = {antigo["produto_id"]} if antigo and antigo["produto_id"] else set()
    estado = d.get("estado") or "confirmado"
    if estado == "remover":
        con.execute("DELETE FROM de_para WHERE cnpj=? AND cod_fornecedor=?", (cnpj, cod))
    elif estado == "ignorar":
        con.execute("INSERT OR REPLACE INTO de_para VALUES(?,?,NULL,NULL,'ignorar',?,?)",
                    (cnpj, cod, quem, agora()))
    else:
        pid = int(d.get("produto_id") or 0)
        fator = num_br(d.get("fator"))
        if not con.execute("SELECT 1 FROM produtos WHERE id=?", (pid,)).fetchone():
            raise ValueError("Escolha o produto.")
        if not 0 < fator <= 100000:
            raise ValueError("Informe quantas unidades vêm em cada caixa/pacote (maior que zero).")
        con.execute("INSERT OR REPLACE INTO de_para VALUES(?,?,?,?,'confirmado',?,?)",
                    (cnpj, cod, pid, fator, quem, agora()))
        afetados.add(pid)
    con.commit()
    recalcular(con, afetados)
    return {"ok": True}


def _digito_ean13(doze):
    soma = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(doze))
    return str((10 - soma % 10) % 10)


def buscar_codigo(con, codigo):
    """Procura pelo código bipado: código de barras, código de caixa (DUN-14), SKU ou
    código do Gestor Z."""
    bruto = "".join(ch for ch in str(codigo) if ch.isdigit())
    if not bruto:
        return []
    tentativas = [norm_ean(bruto)]
    if len(bruto) == 14:      # caixa: dígitos 2 a 13 + verificador recalculado
        doze = bruto[1:13]
        tentativas.append(norm_ean(doze + _digito_ean13(doze)))
    ids = []
    for t in tentativas:
        if not t:
            continue
        for r in con.execute("SELECT id FROM produtos WHERE ean_norm=? ORDER BY ativo DESC, id",
                             (t,)):
            if r["id"] not in ids:
                ids.append(r["id"])
    if not ids:
        for r in con.execute("SELECT id FROM produtos WHERE sku=? OR gz_codigo=?", (bruto, bruto)):
            ids.append(r["id"])
    return [detalhe(con, i) for i in ids[:10]]


def criar_produto_manual(con, d, quem=None):
    """Cadastro na hora, pela tela de conferência. Fica só no painel: não cria SKU
    (o SKU vem do Gestor Z) e não envia nada para as lojas."""
    ean = "".join(c for c in str(d.get("ean") or "") if c.isdigit())
    nome = " ".join(str(d.get("nome") or "").split())
    if not 6 <= len(ean) <= 14:
        raise ValueError("Código de barras inválido.")
    if len(nome) < 2:
        raise ValueError("Escreva o nome do produto.")
    if len(nome) > 150:
        raise ValueError("Nome muito longo (máximo 150 letras).")
    ja = buscar_codigo(con, ean)
    if ja:
        raise ValueError(f"Este código já está cadastrado: {ja[0]['nome']}.")
    preco, custo = num(d.get("preco")), num(d.get("custo"))
    for v in (preco, custo):
        if v is not None and not 0 <= v <= 100000:
            raise ValueError("Preço fora do normal.")
    estoque = d.get("estoque")
    if estoque in (None, ""):
        estoque = None
    else:
        try:
            estoque = int(estoque)
        except (TypeError, ValueError):
            raise ValueError("A quantidade precisa ser um número inteiro.")
        if not 0 <= estoque <= 1_000_000:
            raise ValueError("Quantidade fora do normal.")
    quem = (quem or d.get("quem") or "").strip()[:40] or "painel"
    grupo = " ".join(str(d.get("grupo") or "").split())[:60].upper()   # os grupos do Gestor Z são em maiúsculas
    cat = con.execute("SELECT id, nome FROM categorias_site WHERE id=?", (str(d.get("categoria_id") or ""),)).fetchone()
    if cat and not grupo:
        grupo = cat["nome"].split(" > ")[-1].upper()[:60]
    unidade = (str(d.get("unidade") or "UN").strip().upper() or "UN")[:6]
    obs = str(d.get("obs") or "").strip()[:300]
    cur = con.execute(
        "INSERT INTO produtos(ean,ean_norm,nome,grupo,unidade,custo,preco,ativo,origem,estoque,conferido_em,"
        "obs,atualizado_em,categoria_id,categoria_nome) VALUES(?,?,?,?,?,?,?,1,'manual',?,?,?,?,?,?)",
        (ean, norm_ean(ean), nome, grupo or None, unidade, custo, preco, estoque,
         agora() if estoque is not None else None, obs or None, agora(),
         cat["id"] if cat else None, cat["nome"] if cat else None))
    pid = cur.lastrowid
    con.execute("INSERT INTO historico(produto_id,quando,campo,de,para,origem) VALUES(?,?,?,?,?,?)",
                (pid, agora(), "cadastro", "", "cadastrado à mão na conferência", quem))
    if estoque is not None:
        con.execute("INSERT INTO historico(produto_id,quando,campo,de,para,origem) VALUES(?,?,?,?,?,?)",
                    (pid, agora(), "estoque", "", str(estoque), quem))
    con.commit()
    return detalhe(con, pid)


def registrar_sem_cadastro(con, dados):
    codigo = "".join(ch for ch in str(dados.get("codigo") or "") if ch.isdigit())[:20]
    if not codigo:
        raise ValueError("Código vazio.")
    con.execute("INSERT INTO leituras_sem_cadastro(codigo,quando,quem,obs) VALUES(?,?,?,?)",
                (codigo, agora(), str(dados.get("quem") or "")[:40], str(dados.get("obs") or "")[:200]))
    con.commit()
    return {"ok": True}


def conferidos_recentes(con, limite=30):
    return [dict(r) for r in con.execute(
        "SELECT h.quando, h.para, h.origem, p.id, p.nome, p.ean FROM historico h "
        "JOIN produtos p ON p.id=h.produto_id WHERE h.campo='estoque' "
        "ORDER BY h.id DESC LIMIT ?", (limite,))]


# ----------------------------------------------------------------------- login

def _segredo(con):
    r = con.execute("SELECT valor FROM meta WHERE chave='segredo_sessao'").fetchone()
    if r:
        return r["valor"]
    s = secrets.token_hex(32)
    set_meta(con, "segredo_sessao", s)
    con.commit()
    return s


def criar_sessao(con):
    expira = str(int(time.time()) + SESSAO_DIAS * 86400)
    assin = hmac.new(_segredo(con).encode(), expira.encode(), hashlib.sha256).hexdigest()
    return expira + "." + assin


def sessao_valida(con, valor):
    try:
        expira, assin = valor.split(".", 1)
        certo = hmac.new(_segredo(con).encode(), expira.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(assin, certo) and int(expira) > time.time()
    except (ValueError, AttributeError):
        return False


def tem_senha():
    cfg = carregar_config()
    return bool(cfg["senha"] or cfg["senha_hash"])


def gerar_hash(senha):
    sal = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", senha.encode(), bytes.fromhex(sal), 300_000).hex()
    return f"pbkdf2${sal}${h}"


def senha_confere(senha):
    cfg = carregar_config()
    senha = str(senha)
    if cfg["senha"]:
        return hmac.compare_digest(senha.encode(), cfg["senha"].encode())
    try:
        _, sal, h = cfg["senha_hash"].split("$")
        calc = hashlib.pbkdf2_hmac("sha256", senha.encode(), bytes.fromhex(sal), 300_000).hex()
        return hmac.compare_digest(calc, h)
    except ValueError:
        return False


def definir_senha():
    """Pergunta a senha no teclado e grava só o resumo (hash) no config.json."""
    import getpass
    print("Crie a senha do Painel de Estoque (mínimo de 8 caracteres).")
    print("Ao digitar, nada aparece na tela: é assim mesmo.")
    while True:
        a = getpass.getpass("Senha: ")
        if len(a) < 8:
            print("Muito curta. Use pelo menos 8 caracteres.")
            continue
        if a != getpass.getpass("Repita a senha: "):
            print("As duas não bateram. Tente de novo.")
            continue
        break
    cfg = {}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
    cfg.pop("senha", None)
    cfg["senha_hash"] = gerar_hash(a)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    print("Senha gravada. Feche e abra o painel de novo para valer.")


FALHAS = {}   # ip -> (quantidade, bloqueado_ate)


def tentar_login(con, ip, senha):
    qtd, ate = FALHAS.get(ip, (0, 0))
    if time.time() < ate:
        raise PermissionError("Muitas tentativas. Espere um minuto e tente de novo.")
    if tem_senha() and senha_confere(senha):
        FALHAS.pop(ip, None)
        return criar_sessao(con)
    qtd += 1
    FALHAS[ip] = (0, time.time() + 60) if qtd >= 5 else (qtd, 0)
    raise PermissionError("Senha incorreta.")


MAX_FOTO = 8 * 1024 * 1024


def salvar_foto(con, pid, dados, quem):
    if not con.execute("SELECT 1 FROM produtos WHERE id=?", (pid,)).fetchone():
        raise ValueError("Produto não encontrado.")
    if len(dados) < 1000 or len(dados) > MAX_FOTO or dados[:3] != b"\xff\xd8\xff":
        raise ValueError("A foto precisa ser um JPEG de até 8 MB.")
    os.makedirs(fotos_dir(), exist_ok=True)
    with open(os.path.join(fotos_dir(), f"{pid}.jpg"), "wb") as f:
        f.write(dados)
    quem = (quem or "").strip()[:40] or "painel"
    con.execute("INSERT INTO fotos(produto_id,quando,quem) VALUES(?,?,?) ON CONFLICT(produto_id) "
                "DO UPDATE SET quando=excluded.quando, quem=excluded.quem", (pid, agora(), quem))
    con.execute("INSERT INTO historico(produto_id,quando,campo,de,para,origem) VALUES(?,?,?,?,?,?)",
                (pid, agora(), "foto", "", "foto tirada na prateleira", quem))
    con.commit()
    return detalhe(con, pid)


def zip_fotos(con):
    import re
    import zipfile
    buf, usados = io.BytesIO(), set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for r in con.execute("SELECT p.id, p.sku, p.nome FROM fotos f JOIN produtos p "
                             "ON p.id=f.produto_id ORDER BY p.nome"):
            arq = os.path.join(fotos_dir(), f"{r['id']}.jpg")
            if not os.path.exists(arq):
                continue
            slug = re.sub(r"[^a-z0-9]+", "-", (r["nome"] or "").lower())[:50].strip("-") or "produto"
            nome = f"{r['sku'] or 'SEM-SKU'}__{slug}__01.jpg"
            if nome in usados:
                nome = f"{r['sku'] or 'SEM-SKU'}__{slug}-{r['id']}__01.jpg"
            usados.add(nome)
            z.write(arq, nome)
    return buf.getvalue()


def exportar_csv(con, q, filtro, grupo):
    where, args = _where(q, filtro, grupo)
    saida = io.StringIO()
    w = csv.writer(saida, delimiter=";")
    w.writerow(["sku", "codigo_gestorz", "codigo_de_barras", "produto", "grupo", "categoria_site", "preco",
                "estoque_oficial", "estoque_estimado", "conferido_em", "nuvemshop", "ifood", "99food", "situacao", "obs"])
    linhas = con.execute(f"SELECT p.* FROM produtos p WHERE {where} "
                         "ORDER BY p.nome COLLATE NOCASE", args).fetchall()
    canais = _canais_de(con, [l["id"] for l in linhas]) if len(linhas) < 900 else None
    for l in linhas:
        cs = canais[l["id"]] if canais is not None else _canais_de(con, [l["id"]])[l["id"]]
        por = {c: "; ".join(f"{x['status']} ({x['estoque']})" if x["estoque"] else x["status"]
                            for x in cs if x["canal"] == c) for c in CANAIS}
        w.writerow([l["sku"] or "", l["gz_codigo"] or "", l["ean"] or "", l["nome"],
                    l["grupo"] or "", l["categoria_nome"] or "", "" if l["preco"] is None else f"{l['preco']:.2f}".replace(".", ","),
                    "" if l["estoque"] is None else l["estoque"],
                    "" if l["estimado"] is None else f"{l['estimado']:g}".replace(".", ","),
                    l["conferido_em"] or "",
                    por["nuvemshop"], por["ifood"], por["99food"], l["situacao"] or "", l["obs"] or ""])
    return "﻿" + saida.getvalue()


# -------------------------------------------------------------------- servidor

TAREFA = {"rodando": False, "msg": "", "erro": "", "resultado": None}


def _tarefa(funcao, *args):
    """Roda uma tarefa longa em segundo plano; a tela acompanha por /api/tarefa."""
    def correr():
        TAREFA.update(rodando=True, msg="Começando…", erro="", resultado=None)
        try:
            TAREFA["resultado"] = funcao(*args, lambda m: TAREFA.update(msg=m))
            TAREFA["msg"] = "Concluído"
        except Exception as e:  # noqa: BLE001 - a mensagem vai para a tela
            TAREFA["erro"] = str(e)
        finally:
            TAREFA["rodando"] = False
    if TAREFA["rodando"]:
        raise ValueError("Já tem uma tarefa em andamento. Espere terminar.")
    TAREFA["rodando"] = True
    threading.Thread(target=correr, daemon=True).start()


def _rodar_sync():
    TAREFA.update(rodando=True, msg="Conectando na Nuvemshop", erro="", resultado=None)
    try:
        con = conectar()
        try:
            TAREFA["resultado"] = sync_nuvemshop(con, lambda m: TAREFA.update(msg=m), completo=True)
        finally:
            con.close()
        TAREFA["msg"] = "Concluído"
    except Exception as e:  # noqa: BLE001 - a mensagem vai para a tela
        TAREFA["erro"] = str(e)
    finally:
        TAREFA["rodando"] = False


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, status=200):
        corpo = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corpo)

    def _corpo(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 12_000_000:
            raise ValueError("Arquivo grande demais.")
        return self.rfile.read(n) if n else b""

    def _arquivo(self, nome, tipo):
        with open(os.path.join(STATIC, nome), "rb") as f:
            corpo = f.read()
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store" if nome.endswith(".html") else "max-age=86400")
        self.end_headers()
        self.wfile.write(corpo)

    def _ip(self):
        return (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()

    def _https(self):
        return ((self.headers.get("X-Forwarded-Proto") or "").lower() == "https"
                or (self.headers.get("Origin") or "").startswith("https://"))

    def _origem_ok(self):
        """Recusa chamadas disparadas por outros sites (a página do painel é a única origem)."""
        host = self.headers.get("Host") or ""
        origem = self.headers.get("Origin")
        if origem and urlparse(origem).netloc != host:
            return False
        if not tem_senha() and host.split(":")[0] not in ("127.0.0.1", "localhost"):
            return False          # sem senha, só este computador
        return True

    def _logado(self, con):
        if not tem_senha():
            return True
        for parte in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = parte.strip().partition("=")
            if k == "painel_sessao" and sessao_valida(con, v):
                return True
        return False

    def do_GET(self):
        if not self._origem_ok():
            return self._json({"erro": "Acesso negado."}, 403)
        u = urlparse(self.path)
        qs = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path == "/login":
            return self._arquivo("login.html", "text/html; charset=utf-8")
        if u.path == "/vendor/html5-qrcode.min.js":
            return self._arquivo("vendor/html5-qrcode.min.js", "text/javascript; charset=utf-8")
        con = conectar()
        try:
            if not self._logado(con):
                if u.path.startswith("/api/"):
                    return self._json({"erro": "Entre com a senha.", "login": True}, 401)
                self.send_response(302)
                self.send_header("Location", "/login")
                self.end_headers()
                return
            if u.path in ("/", "/index.html"):
                return self._arquivo("index.html", "text/html; charset=utf-8")
            if u.path == "/conferir":
                return self._arquivo("conferir.html", "text/html; charset=utf-8")
            if u.path == "/api/resumo":
                return self._json(resumo(con))
            if u.path == "/api/produtos":
                return self._json(listar(con, qs.get("q", ""), qs.get("filtro", "todos"),
                                         qs.get("grupo", ""), max(1, int(qs.get("pagina", 1))),
                                         min(50, max(1, int(qs.get("por_pagina", 50))))))
            if u.path == "/api/codigo":
                return self._json({"itens": buscar_codigo(con, qs.get("c", ""))})
            if u.path == "/api/conferidos":
                return self._json({"servidor": VERSAO_SERVIDOR, "itens": conferidos_recentes(con),
                                   "total": con.execute("SELECT COUNT(*) FROM produtos WHERE "
                                                        "estoque IS NOT NULL").fetchone()[0]})
            if u.path == "/api/sem-cadastro":
                return self._json({"itens": [dict(r) for r in con.execute(
                    "SELECT * FROM leituras_sem_cadastro ORDER BY id DESC LIMIT 200")]})
            if u.path == "/api/entradas":
                return self._json(listar_entradas(con, qs.get("estado", "pendente"), qs.get("q", ""),
                                                  max(1, int(qs.get("pagina", 1))),
                                                  so_contados=qs.get("contados") == "1"))
            if u.path == "/api/importacoes":
                return self._json(listar_importacoes(con))
            if u.path == "/api/nuvemshop/previa":
                return self._json(previa_nuvemshop(con, qs.get("modo", "zerar")))
            if u.path == "/api/categorias":
                return self._json({"itens": categorias_lista(con)})
            if u.path == "/api/nuvemshop/cadastrar/previa":
                cats = categorias_lista(con)
                r = previa_cadastrar(con, cats)
                r["erro_categorias"] = "" if cats else "As categorias da loja ainda não foram lidas. Clique em “Atualizar dados” > “Ler a Nuvemshop agora”."
                return self._json(r)
            if u.path == "/api/nuvemshop/cadastrar/backups":
                return self._json({"itens": listar_backups_cadastrar()})
            if u.path == "/api/nuvemshop/publicar/previa":
                return self._json(previa_publicar(con, qs.get("dia", "")))
            if u.path == "/api/nuvemshop/publicar/backups":
                return self._json({"itens": listar_backups_publicar()})
            if u.path == "/api/nuvemshop/fotos/previa":
                return self._json(previa_fotos(con))
            if u.path == "/api/nuvemshop/fotos/backups":
                return self._json({"itens": listar_backups_fotos()})
            if u.path == "/api/nuvemshop/backups":
                return self._json({"itens": listar_backups()})
            if u.path.startswith("/api/foto/"):
                arq = os.path.join(fotos_dir(), f"{int(u.path.rsplit('/', 1)[1])}.jpg")
                if not os.path.exists(arq):
                    return self._json({"erro": "Sem foto."}, 404)
                with open(arq, "rb") as f:
                    corpo = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(corpo)))
                self.send_header("Cache-Control", "private, max-age=86400")
                self.end_headers()
                self.wfile.write(corpo)
                return
            if u.path == "/api/fotos.zip":
                corpo = zip_fotos(con)
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition",
                                 f'attachment; filename="fotos_prateleira_{datetime.now():%Y-%m-%d}.zip"')
                self.send_header("Content-Length", str(len(corpo)))
                self.end_headers()
                self.wfile.write(corpo)
                return
            if u.path.startswith("/api/produto/"):
                d = detalhe(con, int(u.path.rsplit("/", 1)[1]))
                return self._json(d or {"erro": "Produto não encontrado."}, 200 if d else 404)
            if u.path == "/api/tarefa":
                return self._json(TAREFA)
            if u.path == "/api/exportar.csv":
                corpo = exportar_csv(con, qs.get("q", ""), qs.get("filtro", "todos"),
                                     qs.get("grupo", "")).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header("Content-Disposition",
                                 f'attachment; filename="estoque_{datetime.now():%Y-%m-%d}.csv"')
                self.send_header("Content-Length", str(len(corpo)))
                self.end_headers()
                self.wfile.write(corpo)
                return
            self._json({"erro": "Não encontrado."}, 404)
        except Exception as e:  # noqa: BLE001
            self._json({"erro": str(e)}, 500)
        finally:
            con.close()

    def do_POST(self):
        if not self._origem_ok():
            return self._json({"erro": "Acesso negado."}, 403)
        u = urlparse(self.path)
        con = conectar()
        try:
            if u.path == "/api/login":
                dados = json.loads(self._corpo() or b"{}")
                try:
                    sessao = tentar_login(con, self._ip(), dados.get("senha", ""))
                except PermissionError as e:
                    return self._json({"erro": str(e)}, 401)
                corpo = b'{"ok": true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(corpo)))
                self.send_header("Set-Cookie", f"painel_sessao={sessao}; Path=/; HttpOnly; "
                                 f"SameSite=Strict; Max-Age={SESSAO_DIAS * 86400}"
                                 + ("; Secure" if self._https() else ""))
                self.end_headers()
                self.wfile.write(corpo)
                return
            if not self._logado(con):
                return self._json({"erro": "Entre com a senha.", "login": True}, 401)
            if u.path.startswith("/api/produto/"):
                dados = json.loads(self._corpo() or b"{}")
                return self._json(alterar(con, int(u.path.rsplit("/", 1)[1]), dados))
            if u.path.startswith("/api/nuvemshop/produto/"):
                d = json.loads(self._corpo() or b"{}")
                return self._json(enviar_produto_site(con, int(u.path.rsplit("/", 1)[1]), (d.get("quem") or "painel")[:40],
                                                      d.get("campos") or []))
            if u.path == "/api/config/nuvemshop":
                d = json.loads(self._corpo() or b"{}")
                return self._json(conectar_nuvemshop(d.get("token"), d.get("loja")))
            if u.path == "/api/nuvemshop/aplicar":
                d = json.loads(self._corpo() or b"{}")
                _tarefa(aplicar_nuvemshop, d.get("modo", "zerar"), d.get("vids") or [], (d.get("quem") or "painel")[:40])
                return self._json({"ok": True})
            if u.path == "/api/nuvemshop/categorias/mapa":
                d = json.loads(self._corpo() or b"{}")
                return self._json(salvar_mapa(con, d.get("grupo"), d.get("categoria_id"), d.get("categoria_nome")))
            if u.path == "/api/nuvemshop/cadastrar/aplicar":
                d = json.loads(self._corpo() or b"{}")
                _tarefa(aplicar_cadastrar, d.get("itens") or [], (d.get("quem") or "painel")[:40])
                return self._json({"ok": True})
            if u.path == "/api/nuvemshop/cadastrar/desfazer":
                d = json.loads(self._corpo() or b"{}")
                _tarefa(desfazer_cadastrar, str(d.get("arquivo") or ""), (d.get("quem") or "painel")[:40])
                return self._json({"ok": True})
            if u.path == "/api/nuvemshop/publicar/aplicar":
                d = json.loads(self._corpo() or b"{}")
                _tarefa(aplicar_publicar, str(d.get("dia") or ""), d.get("pids") or [], (d.get("quem") or "painel")[:40])
                return self._json({"ok": True})
            if u.path == "/api/nuvemshop/publicar/desfazer":
                d = json.loads(self._corpo() or b"{}")
                _tarefa(desfazer_publicar, str(d.get("arquivo") or ""), (d.get("quem") or "painel")[:40])
                return self._json({"ok": True})
            if u.path == "/api/nuvemshop/fotos/aplicar":
                d = json.loads(self._corpo() or b"{}")
                _tarefa(aplicar_fotos, d.get("pids") or [], (d.get("quem") or "painel")[:40])
                return self._json({"ok": True})
            if u.path == "/api/nuvemshop/fotos/desfazer":
                d = json.loads(self._corpo() or b"{}")
                _tarefa(desfazer_fotos, str(d.get("arquivo") or ""), (d.get("quem") or "painel")[:40])
                return self._json({"ok": True})
            if u.path == "/api/nuvemshop/desfazer":
                d = json.loads(self._corpo() or b"{}")
                _tarefa(desfazer_nuvemshop, str(d.get("arquivo") or ""), (d.get("quem") or "painel")[:40])
                return self._json({"ok": True})
            if u.path.startswith("/api/foto/"):
                return self._json(salvar_foto(con, int(u.path.rsplit("/", 1)[1]), self._corpo(),
                                              unquote(self.headers.get("X-Quem") or "")))
            if u.path == "/api/produto-novo":
                return self._json(criar_produto_manual(con, json.loads(self._corpo() or b"{}")))
            if u.path == "/api/sem-cadastro":
                return self._json(registrar_sem_cadastro(con, json.loads(self._corpo() or b"{}")))
            if u.path == "/api/importar/vendas":
                qp = {k: v[0] for k, v in parse_qs(u.query).items()}
                return self._json(importar_vendas(con, self._corpo(), qp.get("inicio"), qp.get("fim"),
                                                  unquote(qp.get("arquivo", "")),
                                                  unquote(self.headers.get("X-Quem") or "painel")[:40]))
            if u.path == "/api/importar/notas":
                qp = {k: v[0] for k, v in parse_qs(u.query).items()}
                return self._json(importar_notas(con, self._corpo(), unquote(qp.get("arquivo", "")),
                                                 unquote(self.headers.get("X-Quem") or "painel")[:40]))
            if u.path == "/api/entradas/vinculo":
                d = json.loads(self._corpo() or b"{}")
                return self._json(vincular_entrada(con, d, (d.get("quem") or "painel")[:40]))
            if u.path == "/api/entradas/ignorar-lote":
                d = json.loads(self._corpo() or b"{}")
                return self._json(ignorar_lote(con, d.get("itens") or [], (d.get("quem") or "painel")[:40]))
            if u.path == "/api/importacoes/remover":
                return self._json(remover_importacao(con, json.loads(self._corpo() or b"{}").get("id")))
            if u.path == "/api/importar/gestorz":
                return self._json(importar_gestorz(con, self._corpo()))
            if u.path == "/api/importar/painel":
                return self._json(importar_painel(con, self._corpo()))
            if u.path == "/api/sync/nuvemshop":
                if not TAREFA["rodando"]:
                    threading.Thread(target=_rodar_sync, daemon=True).start()
                return self._json({"ok": True})
            self._json({"erro": "Não encontrado."}, 404)
        except (ValueError, KeyError) as e:
            self._json({"erro": str(e)}, 400)
        except Exception as e:  # noqa: BLE001
            self._json({"erro": str(e)}, 500)
        finally:
            con.close()


def backup_banco():
    """Cópia do banco na pasta backups: uma por dia, renovada se tiver mais de 6 horas
    (guarda as 14 mais recentes). Escreve num arquivo temporário e só então troca."""
    try:
        os.makedirs(backups_dir(), exist_ok=True)
        destino = os.path.join(backups_dir(), f"banco_{datetime.now():%Y%m%d}.db")
        velho = not os.path.exists(destino) or time.time() - os.path.getmtime(destino) > 6 * 3600
        if velho and os.path.exists(DB_PATH):
            tmp = destino + ".tmp"
            origem, copia = sqlite3.connect(DB_PATH), sqlite3.connect(tmp)
            origem.backup(copia)
            copia.close()
            origem.close()
            os.replace(tmp, destino)
        antigos = sorted(n for n in os.listdir(backups_dir()) if n.startswith("banco_") and n.endswith(".db"))
        for n in antigos[:-14]:
            os.remove(os.path.join(backups_dir(), n))
    except Exception:  # noqa: BLE001 - backup nunca derruba o painel
        pass


def _rotina_backup():
    while True:
        backup_banco()
        time.sleep(6 * 3600)


def manter_acordado():
    """No Windows, impede o computador de suspender enquanto o painel está aberto."""
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
    except Exception:  # noqa: BLE001 - se não der, o painel funciona do mesmo jeito
        pass


def servir(abrir=True):
    conectar().close()
    threading.Thread(target=_rotina_backup, daemon=True).start()
    manter_acordado()
    if HOST not in ("127.0.0.1", "localhost") and not tem_senha():
        print("Para abrir o painel para outros aparelhos é obrigatório definir uma senha "
              "(PAINEL_SENHA ou \"senha\" no config.json).")
        return 1
    try:
        srv = ThreadingHTTPServer((HOST, PORTA), Handler)
    except OSError:
        print("O painel já está aberto em outra janela preta (porta %d ocupada)." % PORTA)
        print("Feche TODAS as janelas pretas do painel, depois abra o iniciar.bat de novo.")
        print("Isso é necessário depois de atualizar os arquivos, para valer a versão nova.")
        return 1
    url = f"http://127.0.0.1:{PORTA}/"
    print(f"Painel de Estoque aberto em {url}")
    print("Deixe esta janela aberta enquanto usa o painel. Para fechar: Ctrl+C.")
    if not tem_senha():
        print("Sem senha: o painel só responde neste computador. Para usar no celular, "
              "rode definir-senha.bat.")
    if abrir:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nPainel fechado.")


def main(argv):
    if len(argv) < 2:
        return servir()
    cmd = argv[1]
    if cmd == "definir-senha":
        return definir_senha()
    if cmd == "servir":
        return servir(abrir="--sem-navegador" not in argv)
    con = conectar()
    try:
        if cmd == "importar-gestorz":
            print(importar_gestorz(con, argv[2]))
        elif cmd == "importar-painel":
            print(importar_painel(con, argv[2]))
        elif cmd == "sync-nuvemshop":
            print(sync_nuvemshop(con, print))
        else:
            print(__doc__)
            return 1
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv) or 0)
