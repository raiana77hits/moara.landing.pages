#!/usr/bin/env python3
# versao-do-painel: 9
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
import math
import zlib
import os
import re
import secrets
import sqlite3
import sys
import shutil
import threading
import unicodedata
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
import zipfile
from xml.sax.saxutils import escape as xml_escape
from datetime import datetime, timedelta
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
VERSAO_SERVIDOR = "9"      # a tela compara com este número para detectar janela antiga aberta

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
    preco REAL,              -- preço de venda no painel (pode ser editado à mão)
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

-- catálogo do iFood (planilha exportada do portal), para saber ruptura e o que já existe lá
CREATE TABLE IF NOT EXISTS ifood_catalogo (
    ean TEXT PRIMARY KEY,
    nome TEXT, em_venda INTEGER, bloqueio TEXT, preco REAL, qtd REAL, dt TEXT
);

-- catálogo da 99Food (planilha "Baixar dados de todos os itens"), guardado inteiro para reeditar
CREATE TABLE IF NOT EXISTS n99_catalogo (
    id TEXT PRIMARY KEY,
    ean TEXT, nome TEXT, descricao TEXT, id_ext TEXT, preco REAL, preco_txt TEXT, qtd REAL,
    categoria TEXT, disp TEXT, tags TEXT, peso TEXT, volume TEXT, armaz TEXT, embal TEXT
);

-- pedidos do site (lidos da Nuvemshop, só leitura)
CREATE TABLE IF NOT EXISTS pedidos_site (
    id INTEGER PRIMARY KEY,
    numero TEXT, criado_em TEXT, status TEXT, pagamento TEXT, total REAL
);
CREATE TABLE IF NOT EXISTS pedidos_itens (
    pedido_id INTEGER NOT NULL,
    variacao_id TEXT, produto_site TEXT, sku TEXT, nome TEXT, qtd REAL
);
CREATE INDEX IF NOT EXISTS ix_pi_ped ON pedidos_itens(pedido_id);
CREATE INDEX IF NOT EXISTS ix_pi_var ON pedidos_itens(variacao_id);

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
    if "preco_gz" not in colunas:           # preço OFICIAL: só muda quando o cadastro do Gestor Z é importado
        con.execute("ALTER TABLE produtos ADD COLUMN preco_gz REAL")
        con.execute("UPDATE produtos SET preco_gz=preco WHERE origem='gestorz' AND preco > 0")
        con.commit()
    for col, tipo in (("estimado", "REAL"), ("est_aviso", "TEXT"), ("categoria_id", "TEXT"),
                      ("categoria_nome", "TEXT"), ("ifood_depto", "TEXT"), ("ifood_cat", "TEXT")):
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


def preco_efetivo(p):
    """O preço que vale: o do Gestor Z (oficial) quando existe; senão o que está no painel."""
    try:
        oficial = p["preco_gz"]
    except (IndexError, KeyError):
        oficial = None
    if oficial and oficial > 0:
        return oficial
    try:
        return p["preco"]
    except (IndexError, KeyError):
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
    if isinstance(origem, (bytes, bytearray)) and bytes(origem)[:2] == b"PK":   # veio como planilha .xlsx
        tabela = ler_xlsx(bytes(origem), exigir=[["ID PRODUTO"], ["DESCRICAO"]])
        cab = [str(c or "").strip() for c in (tabela[0] if tabela else [])]
        linhas = [{cab[i]: ("" if i >= len(r) or r[i] is None else str(r[i]).strip()) for i in range(len(cab)) if cab[i]}
                  for r in tabela[1:] if any(x not in (None, "") for x in r)]
    else:
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
            preco=num(l.get("venda")), preco_gz=num(l.get("venda")), ativo=0 if (l.get("ativo") or "S").upper() == "N" else 1,
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
        "pasta_backup_extra": os.environ.get("PAINEL_BACKUP_EXTRA") or str(cfg.get("pasta_backup_extra", "") or ""),
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

    caminhos = _caminhos_categorias(vistas)
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


# ------------------------------------------------------------- iFood (API oficial)

IFOOD_API = os.environ.get("IFOOD_API_BASE", "https://merchant-api.ifood.com.br").rstrip("/")
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_IFOOD_TOKEN = {}


def _ler_config_bruto():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def ifood_config(ambiente):
    """Chaves do iFood do ambiente 'teste' ou 'real'. O ID é sempre o código no formato 8-4-4-4-12; se o
    ID e o segredo vierem trocados, o painel acerta sozinho."""
    ambiente = "real" if ambiente == "real" else "teste"
    cfg = _ler_config_bruto()
    pre = "ifood_" + ambiente + "_"
    a = cfg.get(pre + "client_id", "") or os.environ.get("IFOOD_CLIENT_ID" if ambiente == "real" else "IFOOD_TEST_CLIENT_ID", "")
    b = cfg.get(pre + "client_secret", "") or os.environ.get("IFOOD_CLIENT_SECRET" if ambiente == "real" else "IFOOD_TEST_CLIENT_SECRET", "")
    a, b = a.strip(), b.strip()
    if b and _UUID.match(b) and not _UUID.match(a):
        a, b = b, a                                    # vieram trocados
    merchant = (cfg.get(pre + "merchant_id", "") or (os.environ.get("IFOOD_MERCHANT_ID", "") if ambiente == "real" else "")).strip()
    return {"ambiente": ambiente, "client_id": a, "client_secret": b, "merchant_id": merchant}


def _ifood_http(metodo, caminho, corpo=None, token=None, form=None):
    import gzip
    url = IFOOD_API + caminho
    cab = {"Accept": "application/json", "Accept-Encoding": "gzip", "User-Agent": "Painel de Estoque"}
    if form is not None:
        dados = urllib.parse.urlencode(form).encode()
        cab["Content-Type"] = "application/x-www-form-urlencoded"
    elif corpo is not None:
        dados = json.dumps(corpo).encode()
        cab["Content-Type"] = "application/json"
    else:
        dados = None
    if token:
        cab["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=dados, method=metodo, headers=cab)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            bruto = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                bruto = gzip.decompress(bruto)
            if not bruto.strip():
                return resp.status, {}
            try:
                return resp.status, json.loads(bruto)
            except ValueError:                       # resposta em texto simples
                return resp.status, {"texto": bruto[:500].decode("utf-8", "replace")}
    except urllib.error.HTTPError as e:
        bruto = e.read()
        try:
            if e.headers.get("Content-Encoding") == "gzip":
                bruto = gzip.decompress(bruto)
            corpo_err = json.loads(bruto) if bruto.strip() else {}
        except Exception:  # noqa: BLE001
            corpo_err = {"texto": bruto[:200].decode("utf-8", "replace")}
        return e.code, corpo_err


def ifood_token(ambiente):
    cfg = ifood_config(ambiente)
    if not cfg["client_id"] or not cfg["client_secret"]:
        raise ValueError("Falta o ID e o segredo do iFood (" + ambiente + ").")
    ja = _IFOOD_TOKEN.get(ambiente)
    if ja and ja["ate"] > time.time() + 60:
        return ja["token"]
    codigo, r = _ifood_http("POST", "/authentication/v1.0/oauth/token", form={
        "grantType": "client_credentials", "clientId": cfg["client_id"], "clientSecret": cfg["client_secret"]})
    if codigo != 200:
        msg = str((r.get("error") or {}).get("message") or r)
        if codigo == 403:
            raise ValueError("O iFood conhece o aplicativo, mas ele ainda não tem permissão liberada "
                             "(a homologação do módulo precisa ser aprovada). Resposta: " + msg[:120])
        if codigo in (400, 401):
            raise ValueError("O iFood recusou o ID/segredo. Confira se não estão trocados ou com espaço. Resposta: " + msg[:120])
        raise ValueError(f"O iFood respondeu {codigo}: {msg[:150]}")
    _IFOOD_TOKEN[ambiente] = {"token": r["accessToken"], "ate": time.time() + int(r.get("expiresIn", 3600))}
    return r["accessToken"]


def estado_ifood():
    cfg_arquivo = _ler_config_bruto()
    saida = {}
    for amb in ("teste", "real"):
        c = ifood_config(amb)
        saida[amb] = {"id": bool(c["client_id"]), "segredo": bool(c["client_secret"]), "loja": bool(c["merchant_id"]),
                      "loja_id_fim": c["merchant_id"][-6:] if c["merchant_id"] else ""}
    saida["ambiente"] = "real" if cfg_arquivo.get("ifood_ambiente") == "real" else "teste"
    return saida


def conectar_ifood(d):
    """Guarda as chaves no config.json (neste computador) e confere com o iFood. Só leitura."""
    amb = "real" if d.get("ambiente") == "real" else "teste"
    dados = _ler_config_bruto()
    pre = "ifood_" + amb + "_"
    for campo in ("client_id", "client_secret", "merchant_id"):
        v = str(d.get(campo) or "").strip()
        if v:
            dados[pre + campo] = v
    dados["ifood_ambiente"] = amb
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(dados, f, indent=2, ensure_ascii=False)
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass
    _IFOOD_TOKEN.pop(amb, None)
    return testar_ifood(amb)


def testar_ifood(amb):
    amb = "real" if amb == "real" else "teste"
    ifood_token(amb)                              # levanta ValueError com a explicação, se falhar
    r = {"ok": True, "ambiente": amb, "token": "ok", "lojas": []}
    codigo, lojas = _ifood_http("GET", "/merchant/v1.0/merchants", token=_IFOOD_TOKEN[amb]["token"])
    if codigo == 200 and isinstance(lojas, list):
        r["lojas"] = [{"id_fim": str(x.get("id", ""))[-6:], "nome": str(x.get("name") or "")[:60]} for x in lojas[:10]]
    else:
        r["aviso_lojas"] = f"Não consegui listar as lojas (o iFood respondeu {codigo})."
    cfg = ifood_config(amb)
    if cfg["merchant_id"] and r["lojas"]:
        r["loja_configurada_aparece"] = any(cfg["merchant_id"].endswith(x["id_fim"]) for x in r["lojas"])
    return r


def ifood_item_corpo(p, divisor=0.70):
    """Um produto do painel no formato da API de Item do iFood (POST /item/v1.0/ingestion).
    Devolve (corpo, avisos). Campos: barcode e name são obrigatórios; o resto é opcional."""
    avisos = []
    ean = "".join(c for c in str(p["ean"] or "") if c.isdigit())
    if not 6 <= len(ean) <= 14:
        avisos.append("sem código de barras válido")
    elif ean.startswith("2"):
        avisos.append("código interno do caixa (começa com 2): confirme se serve de código de barras no iFood")
    corpo = {"barcode": ean, "name": " ".join(str(p["nome"] or "").split())[:150]}
    plu = str(p["sku"] or p["gz_codigo"] or "").strip()
    if plu:
        corpo["plu"] = plu
    est, cont = p["estimado"], p["estoque"]
    if est is not None and est <= 0 and cont is not None and cont > 0:
        # a estimativa zerou, mas a contagem diz que há estoque: o mais provável é erro no período das vendas
        avisos.append(f"estimado zerou ({est:g}) mas a contagem é {cont}: enviando a contagem. Confira o período das vendas importadas")
        base = cont
    else:
        base = est if est is not None else cont
    qtd = None if base is None else max(0, int(math.floor(base)))
    corpo["active"] = True if qtd is None else qtd > 0
    pe = preco_efetivo(p)
    if pe and pe > 0:
        corpo["prices"] = {"price": round(pe / divisor + 1e-9, 2)}
    else:
        avisos.append("sem preço")
    det = {}
    if p["foto_url"]:
        det["imageUrl"] = p["foto_url"]
    det["description"] = corpo["name"]
    un = str(p["unidade"] or "").strip().upper()
    if un in ("UN", "KG", "G", "L", "ML"):
        det["unit"] = un
    cat = {}
    if p["grupo"]:
        cat["department"] = str(p["grupo"]).title()[:60]
    if p["ifood_cat"]:                      # categoria escolhida à mão para o iFood: vale mais que o grupo do Gestor Z
        cat = {"department": str(p["ifood_depto"] or p["grupo"] or p["ifood_cat"]).title()[:60], "category": str(p["ifood_cat"])[:60]}
    elif p["categoria_nome"]:
        partes = [x.strip() for x in str(p["categoria_nome"]).split(">")]
        cat["category"] = partes[-1][:60]
        if len(partes) > 2:
            cat["subCategory"] = partes[-1][:60]
            cat["category"] = partes[-2][:60]
    if cat:
        det["categorization"] = cat
    corpo["details"] = det
    if qtd is not None:
        corpo["inventory"] = {"stock": qtd}
    else:
        avisos.append("sem contagem (o estoque não vai no envio)")
    return corpo, avisos


MAX_IFOOD_TESTE = 20

# --- listas rápidas e comparação de canais (para escolher o que subir sem digitar nome de produto)
_FLAG_SITE = "EXISTS (SELECT 1 FROM canais c WHERE c.produto_id=p.id AND c.canal='nuvemshop')"
_FLAG_IFOOD = ("(EXISTS (SELECT 1 FROM canais c WHERE c.produto_id=p.id AND c.canal='ifood') OR EXISTS "
               "(SELECT 1 FROM historico h WHERE h.produto_id=p.id AND h.campo LIKE 'iFood (loja real)%'))")
_FLAG_99 = "EXISTS (SELECT 1 FROM canais c WHERE c.produto_id=p.id AND c.canal='99food')"
def _estoque_para_ifood(p):
    """Mesma regra do envio: estimado quando faz sentido, senão a contagem. None = sem contagem."""
    est, cont = p["estimado"], p["estoque"]
    if est is not None and est <= 0 and cont is not None and cont > 0:
        base = cont
    else:
        base = est if est is not None else cont
    return None if base is None else max(0, int(math.floor(base)))


def motivos_ifood(p, cat, divisor=0.70):
    """Por que um produto já no catálogo do iFood precisa de atualização (preço, estoque ou ativo)."""
    if not cat:
        return []
    m = []
    pe = preco_efetivo(p)
    if pe and pe > 0 and cat["preco"] is not None:
        esp = round(pe / divisor + 1e-9, 2)
        if abs(cat["preco"] - esp) > TOLERANCIA_PRECO:
            m.append(f"preço: iFood R$ {cat['preco']:.2f} → R$ {esp:.2f}".replace(".", ","))
    qtd = _estoque_para_ifood(p)
    if qtd is not None:
        if cat["qtd"] is not None and int(cat["qtd"]) != qtd:
            m.append(f"estoque: iFood {int(cat['qtd'])} → {qtd}")
        if cat["em_venda"] is not None and bool(cat["em_venda"]) != (qtd > 0):
            m.append("está INATIVO no iFood e tem estoque: ativar" if qtd > 0 else "está ativo no iFood e o estoque é zero: desativar")
    return m


def ids_atualizar_ifood(con, divisor=0.70):
    cat = {norm_ean(r["ean"]): r for r in con.execute("SELECT ean, preco, qtd, em_venda FROM ifood_catalogo")}
    return [p["id"] for p in con.execute("SELECT * FROM produtos") if p["ean"] and motivos_ifood(p, cat.get(norm_ean(p["ean"])), divisor)]


def listas_ifood(con=None):
    return {
        "contados_hoje": ("Contados hoje", "p.estoque IS NOT NULL AND substr(p.conferido_em,1,10)=date('now','localtime')"),
        "novos_ifood": ("Contados desde o último envio ao iFood", FILTROS["novo_ifood"]),
        "cadastrados": ("Cadastrados à mão no painel", "p.origem='manual'"),
        "movimento": ("Estoque mudou por nota ou venda depois da contagem",
                      "p.estoque IS NOT NULL AND p.estimado IS NOT NULL AND CAST(round(p.estimado) AS INTEGER) <> p.estoque"),
        "contados": ("Todos os contados", "p.estoque IS NOT NULL"),
        "site_sem_ifood": ("Estão no site e não estão no iFood", f"{_FLAG_SITE} AND NOT {_FLAG_IFOOD}"),
        "ifood_sem_99": ("Estão no iFood e não estão na 99Food", f"{_FLAG_IFOOD} AND NOT {_FLAG_99}"),
        "sem_ifood": ("Não estão no iFood (qualquer origem)", f"NOT {_FLAG_IFOOD}"),
        "zerados": ("Estoque zero: desativar no iFood", f"p.estoque = 0 AND {_FLAG_IFOOD}"),
        "preco_ifood": ("Preço ou estoque diferente do iFood",
                        ("p.id IN (" + ",".join(str(i) for i in ids_atualizar_ifood(con)) + ")") if con is not None else "0"),
    }
LIMITE_LISTA_IFOOD = 300


# produto que já subiu à loja real depois da última contagem: não precisa subir de novo
_JA_ENVIADO = ("EXISTS (SELECT 1 FROM historico h WHERE h.produto_id=p.id AND h.campo='iFood (loja real)' "
               "AND h.quando >= COALESCE(p.conferido_em,''))")


def candidatos_ifood(con, lista, divisor=0.70, mostrar_enviados=False):
    """Produtos de uma lista rápida (ou de uma combinação de canais 'combo:SIN', ex.: combo:101 = site e 99, sem iFood),
    com o que falta para ir ao iFood. Só leitura: não envia nada."""
    if lista.startswith("combo:") and len(lista) == 9 and set(lista[6:]) <= {"0", "1"}:
        b = lista[6:]
        nome = "Site " + ("sim" if b[0] == "1" else "não") + " · iFood " + ("sim" if b[1] == "1" else "não") + " · 99Food " + ("sim" if b[2] == "1" else "não")
        cond = " AND ".join((f, f"NOT {f}")[x == "0"] for f, x in ((_FLAG_SITE, b[0]), (_FLAG_IFOOD, b[1]), (_FLAG_99, b[2])))
    elif lista in listas_ifood():
        nome, cond = listas_ifood(con)[lista]
        if lista == "preco_ifood" and cond == "p.id IN ()":
            cond = "0"
    else:
        raise ValueError("Lista desconhecida.")
    escondidos = 0
    if not mostrar_enviados:
        escondidos = con.execute(f"SELECT COUNT(*) FROM produtos p WHERE ({cond}) AND {_JA_ENVIADO}").fetchone()[0]
        cond = f"({cond}) AND NOT {_JA_ENVIADO}"
    linhas = con.execute(
        f"SELECT p.*, {_FLAG_SITE} AS f_site, {_FLAG_IFOOD} AS f_ifood, {_FLAG_99} AS f_99 FROM produtos p "
        f"WHERE ({cond}) ORDER BY p.conferido_em DESC, p.nome COLLATE NOCASE LIMIT ?", (LIMITE_LISTA_IFOOD + 1,)).fetchall()
    itens = []
    cat_if = {norm_ean(r["ean"]): r for r in con.execute("SELECT ean, preco, qtd, em_venda FROM ifood_catalogo")}
    ifood_atual = {k: v["preco"] for k, v in cat_if.items()}
    for p in linhas[:LIMITE_LISTA_IFOOD]:
        corpo, avisos = ifood_item_corpo(p, divisor)
        itens.append({"id": p["id"], "nome": p["nome"], "ean": p["ean"], "preco": preco_efetivo(p),
                      "preco_ifood": (corpo.get("prices") or {}).get("price"), "estoque": p["estoque"],
                      "ifood_atual": ifood_atual.get(norm_ean(p["ean"])),
                      "motivos": motivos_ifood(p, cat_if.get(norm_ean(p["ean"])), divisor),
                      "estimado": p["estimado"], "conferido_em": p["conferido_em"], "site": bool(p["f_site"]),
                      "ifood": bool(p["f_ifood"]), "n99": bool(p["f_99"]), "avisos": avisos,
                      "pronto": not any(a.startswith(("sem preço", "sem código")) for a in avisos)})
    return {"lista": lista, "nome": nome, "itens": itens, "cortou": len(linhas) > LIMITE_LISTA_IFOOD, "escondidos": escondidos}


def comparar_canais(con):
    """Quantos produtos estão em cada combinação de Nuvemshop / iFood / 99Food (só leitura)."""
    r = con.execute(f"SELECT {_FLAG_SITE} a, {_FLAG_IFOOD} b, {_FLAG_99} c, COUNT(*) n FROM produtos p GROUP BY a, b, c").fetchall()
    return {"combinacoes": [{"codigo": f"{x['a']}{x['b']}{x['c']}", "site": bool(x["a"]), "ifood": bool(x["b"]),
                             "n99": bool(x["c"]), "n": x["n"]} for x in r],
            "listas": [{"chave": k, "nome": v[0]} for k, v in listas_ifood().items()]}


def _ifood_parcial(corpo, promo=None):
    """PATCH: só barcode e name (obrigatórios) + o que muda (preço, promoção, estoque)."""
    p = {"barcode": corpo["barcode"], "name": corpo["name"]}
    if "prices" in corpo:
        p["prices"] = dict(corpo["prices"])
    if promo is not None:
        base = (p.get("prices") or {}).get("price")
        if base is None or not 0 < promo < base:
            raise ValueError("O preço promocional precisa ser maior que zero e menor que o preço de venda.")
        p.setdefault("prices", {})["promotionPrice"] = round(promo, 2)
    if "inventory" in corpo:
        p["inventory"] = dict(corpo["inventory"])
    return p


def previa_ifood_item(con, ids, divisor=0.70, modo="post", promo=None, limite=None):
    modo = modo if modo in ("post", "patch", "reset", "desativar", "preco", "atualizar") else "post"
    ids = list(dict.fromkeys(int(i) for i in ids))
    limite = limite or LIMITE_IFOOD_REAL
    if len(ids) > limite:      # antes o painel cortava a lista em 20 sem avisar
        raise ValueError(f"Marque no máximo {limite} produtos por vez (você marcou {len(ids)}). Envie em lotes.")
    itens = []
    for pid in ids:
        p = con.execute("SELECT * FROM produtos WHERE id=?", (pid,)).fetchone()
        if not p:
            continue
        corpo, avisos = ifood_item_corpo(p, divisor)
        desativar_bloqueia = False
        if modo == "desativar":
            if p["estoque"] != 0:
                avisos.append("só desativa produto contado com estoque 0 (este não está zerado)")
                desativar_bloqueia = True
            corpo = dict(corpo, active=False, inventory={"stock": 0})
        if modo == "atualizar":             # preço + estoque + ativo/inativo; sem categoria, foto nem descrição
            leve = {"barcode": corpo["barcode"], "name": corpo["name"]}
            if "prices" in corpo:
                leve["prices"] = corpo["prices"]
            if "inventory" in corpo:         # com contagem: estoque e ativo (estoque > 0) juntos
                leve["inventory"] = corpo["inventory"]
                leve["active"] = corpo["inventory"]["stock"] > 0
            corpo = leve
            if "prices" not in corpo and "inventory" not in corpo:
                avisos.append("sem preço e sem contagem: nada para atualizar")
                desativar_bloqueia = True
        if modo == "preco":                 # muda só o preço: não mexe em estoque nem em ativo
            corpo = {"barcode": corpo["barcode"], "name": corpo["name"], **({"prices": corpo["prices"]} if "prices" in corpo else {})}
            if "prices" not in corpo:
                avisos.append("sem preço (nem oficial nem no painel): nada para mudar")
                desativar_bloqueia = True
        if modo == "patch":
            corpo = _ifood_parcial(corpo, promo)
        elif promo is not None:
            corpo = dict(corpo)
            base = (corpo.get("prices") or {}).get("price")
            if base is None or not 0 < promo < base:
                raise ValueError("O preço promocional precisa ser maior que zero e menor que o preço de venda.")
            corpo["prices"] = dict(corpo["prices"], promotionPrice=round(promo, 2))
        bloqueia = desativar_bloqueia or any(a.startswith("sem código de barras") for a in avisos)
        if modo in ("post", "reset") and "prices" not in corpo:
            avisos.append("sem preço: o iFood não consegue vender; cadastre o preço antes de enviar")
            bloqueia = True
        if modo == "patch" and "prices" not in corpo and "inventory" not in corpo:
            avisos.append("nada para atualizar (sem preço e sem estoque)")
            bloqueia = True
        cz = (corpo.get("details") or {}).get("categorization") or {}
        ja = con.execute("SELECT 1 FROM produtos p WHERE p.id=? AND " + _FLAG_IFOOD, (pid,)).fetchone() is not None
        itens.append({"pid": pid, "nome": p["nome"], "avisos": avisos, "corpo": corpo, "bloqueia": bloqueia,
                      "resumo": {"preco": (corpo.get("prices") or {}).get("price"), "estoque": (corpo.get("inventory") or {}).get("stock"),
                                 "ativo": corpo.get("active"), "imagem": bool((corpo.get("details") or {}).get("imageUrl")),
                                 "departamento": cz.get("department", ""), "categoria": cz.get("category", ""),
                                 "subcategoria": cz.get("subCategory", ""), "ja_no_ifood": ja}})
    end = {"post": "POST /item/v1.0/ingestion/{loja}?reset=false  (cria ou atualiza tudo)",
           "patch": "PATCH /item/v1.0/ingestion/{loja}  (só preço, promoção e estoque)",
           "reset": "POST /item/v1.0/ingestion/{loja}?reset=true  (CARGA COMPLETA: substitui o catálogo da loja de teste)",
           "desativar": "POST /item/v1.0/ingestion/{loja}?reset=false  (DESATIVAR: estoque 0 e produto inativo)",
           "preco": "PATCH /item/v1.0/ingestion/{loja}  (SÓ O PREÇO)",
           "atualizar": "POST (com contagem: preço, estoque e ativo) e PATCH (sem contagem: só preço)  /item/v1.0/ingestion/{loja}"}[modo]
    return {"endereco": end, "modo": modo, "itens": itens, "enviaveis": sum(1 for i in itens if not i["bloqueia"])}


def _ifood_loja_teste():
    cfg = ifood_config("teste")
    if cfg["merchant_id"]:
        return cfg["merchant_id"]
    codigo, lojas = _ifood_http("GET", "/merchant/v1.0/merchants", token=ifood_token("teste"))
    if codigo == 200 and isinstance(lojas, list) and len(lojas) == 1:
        return str(lojas[0]["id"])
    raise ValueError("Informe o ID da loja de teste na tela de conexão do iFood.")


LIMITE_IFOOD_REAL_PRIMEIRO = 1      # o primeiro envio à loja real é de UM produto só
LIMITE_IFOOD_REAL = 100             # depois disso, no máximo 100 por envio


def enviar_ifood_item(con, ids, ambiente="teste", quem="painel", modo="post", promo=None, confirmo_reset=False,
                      confirmo_real=False):
    """Envia produtos ao iFood (API Item). 'teste' = loja de teste. 'real' = loja real, liberada depois da
    homologação, com travas: nunca faz carga completa (reset), exige digitar ENVIAR, a chave e a loja reais
    configuradas, o primeiro envio é de 1 produto e depois no máximo 100 por vez.
    post = cria/atualiza (reset=false) | patch = só preço, promoção e estoque | reset = carga completa (só teste)."""
    if ambiente not in ("teste", "real"):
        raise ValueError("Ambiente desconhecido.")
    modo = modo if modo in ("post", "patch", "reset", "desativar", "preco", "atualizar") else "post"
    real = ambiente == "real"
    if real:
        if modo == "reset":
            raise ValueError("A carga completa (reset=true) nunca é feita na loja real pelo painel: ela substitui o catálogo inteiro.")
        if not confirmo_real:
            raise ValueError("Para enviar à loja real, digite ENVIAR para confirmar.")
        cfg_real = ifood_config("real")
        if not (cfg_real["client_id"] and cfg_real["client_secret"] and cfg_real["merchant_id"]):
            raise ValueError("Faltam o ID, o segredo e o ID da loja REAL na tela de conexão do iFood (marque 'Loja real').")
        primeiro = not con.execute("SELECT 1 FROM meta WHERE chave='ifood_real_primeiro_ok'").fetchone()
        limite = LIMITE_IFOOD_REAL_PRIMEIRO if primeiro else LIMITE_IFOOD_REAL
        if len(set(ids)) > limite:
            raise ValueError(f"Marque no máximo {limite} produto(s) por envio à loja real"
                             + (" (o primeiro envio é de 1 produto só, para conferir no iFood antes de enviar mais)." if primeiro else "."))
    if modo == "reset" and not confirmo_reset:
        raise ValueError("A carga completa (reset=true) substitui o catálogo da loja de teste. Confirme digitando RESET.")
    pre = previa_ifood_item(con, ids, modo=modo, promo=promo, limite=(LIMITE_IFOOD_REAL if real else MAX_IFOOD_TESTE))
    itens = [i for i in pre["itens"] if not i["bloqueia"]]
    if not itens:
        raise ValueError("Nenhum produto pode ser enviado: " + "; ".join(sorted({a for i in pre["itens"] for a in i["avisos"] if a.startswith(("sem ", "nada "))}))[:220])
    loja = cfg_real["merchant_id"] if real else _ifood_loja_teste()
    token = ifood_token("real" if real else "teste")
    corpo = [i["corpo"] for i in itens]
    base_url = f"/item/v1.0/ingestion/{loja}"
    if modo == "atualizar":        # quem tem contagem (ativo/estoque) vai por POST; quem não tem, só o preço, por PATCH
        grupos = [("POST", base_url + "?reset=false", [c for c in corpo if "active" in c]),
                  ("PATCH", base_url, [c for c in corpo if "active" not in c])]
    elif modo in ("patch", "preco"):
        grupos = [("PATCH", base_url, corpo)]
    else:
        grupos = [("POST", base_url + f"?reset={'true' if modo == 'reset' else 'false'}", corpo)]
    respostas, codigos = [], []
    for metodo, caminho, parte in grupos:
        if not parte:
            continue
        espera = 5
        for tentativa in range(4):                   # 429 = muitas requisições: espera e tenta de novo
            cod, r_ = _ifood_http(metodo, caminho, corpo=parte, token=token)
            if cod != 429 or tentativa == 3:
                break
            time.sleep(espera)
            espera *= 2
        codigos.append(cod)
        respostas.append({"metodo": metodo, "produtos": len(parte), "status": cod, "resposta": r_})
    ok_tudo = bool(codigos) and all(200 <= c < 300 for c in codigos)
    codigo = next((c for c in codigos if not 200 <= c < 300), codigos[-1] if codigos else 0)
    resp = respostas[0]["resposta"] if len(respostas) == 1 else respostas
    metodo = "+".join(g["metodo"] for g in respostas)
    caminho = base_url
    os.makedirs(backups_dir(), exist_ok=True)
    nome = f"ifood_api_{'real' if real else 'teste'}_{modo}_{datetime.now():%Y%m%d_%H%M%S}.json"
    with open(os.path.join(backups_dir(), nome), "w", encoding="utf-8") as f:
        json.dump({"quando": agora(), "quem": quem, "loja_fim": loja[-6:], "endereco": f"{metodo} {caminho.split('/')[-1]}",
                   "modo": modo, "status": codigo, "enviado": corpo, "resposta": resp}, f, ensure_ascii=False, indent=2)
    if ok_tudo:
        for i in itens:
            con.execute("INSERT INTO historico(produto_id,quando,campo,de,para,origem) VALUES(?,?,?,?,?,?)",
                        (i["pid"], agora(), "iFood (loja real)" if real else "iFood (loja de teste)", "", f"enviado pela API ({modo})", quem))
        if real:
            set_meta(con, "ifood_real_primeiro_ok", agora())
        con.commit()
    msg = str((resp.get("error") or {}).get("message") or "")[:200] if isinstance(resp, dict) else ""
    return {"ok": ok_tudo, "status": codigo, "enviados": len(itens), "modo": modo,
            "resposta": resp if isinstance(resp, (dict, list)) else str(resp)[:300], "mensagem": msg, "backup": nome}


def previa_nuvemshop(con, modo="zerar"):
    """O que mudaria no site com base no estoque contado. Não altera nada."""
    modo = modo if modo in ("repor", "estimado") else "zerar"
    itens, ja_igual, varias, conferir = [], 0, {}, 0
    for r in con.execute(
            "SELECT p.id pid, p.nome, p.sku, p.estoque, p.estimado, k.id_externo prod_site, k.id_variacao var_site, "
            "k.estoque site, k.status, m.n FROM produtos p JOIN canais k ON k.produto_id=p.id "
            "AND k.canal='nuvemshop' JOIN (SELECT produto_id, COUNT(*) n FROM canais WHERE canal='nuvemshop' "
            "GROUP BY produto_id) m ON m.produto_id=p.id WHERE p.estoque IS NOT NULL ORDER BY p.nome"):
        if r["n"] > 1:
            # um produto contado ligado a várias variações do site (cores, tamanhos): não dá para
            # saber qual variação a contagem representa, então fica para decidir à mão
            varias[r["pid"]] = r["nome"]
            continue
        site, contado = (r["site"] or "").strip(), r["estoque"]
        if modo == "estimado":
            # estimado = contagem + notas - vendas. Só vale se houve movimento; nunca zera o site por estimativa.
            est = r["estimado"]
            if est is None or round(est) == contado:
                alvo, motivo = None, None
            elif est <= 0:
                conferir += 1                     # estimado zerou: confira na prateleira antes de mexer no site
                alvo, motivo = None, None
            elif site != str(round(est)):
                alvo, motivo = int(round(est)), f"estimado (contado {contado}, hoje {round(est)})"
            else:
                alvo, motivo = None, None
        elif contado == 0:
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
    return {"modo": modo, "conferir_estimado_zerado": conferir, "conferidos": conferidos,
            "mudancas": len(itens), "ja_certos": ja_igual,
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


def _caminhos_categorias(vistas):
    """vistas: id -> (nome, pai). Devolve id -> 'Pai > Filha'."""
    def caminho(cid, n=0):
        nome, pai = vistas.get(cid, ("", None))
        return (caminho(pai, n + 1) + " > " if pai and pai in vistas and n < 6 else "") + nome
    return {cid: caminho(cid) for cid in vistas}


def ler_categorias(con):
    """Lê só a lista de categorias da loja (uma chamada rápida) e guarda no painel."""
    cfg = _exigir_nuvemshop()
    vistas = {}
    for c in categorias_nuvemshop_cruas(cfg):
        vistas[str(c.get("id"))] = (_texto(c.get("name")), str(c["parent"]) if c.get("parent") else None)
    if not vistas:
        raise RuntimeError("A loja não tem nenhuma categoria cadastrada (ou o token não enxerga as categorias).")
    con.execute("DELETE FROM categorias_site")
    con.executemany("INSERT OR REPLACE INTO categorias_site(id, nome) VALUES(?,?)", list(_caminhos_categorias(vistas).items()))
    con.commit()
    return {"ok": True, "categorias": len(vistas)}


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


# ------------------------------------------- organizar categorias (sugestão automática)

def _tokens_cat(t):
    """Palavras do nome na ordem, sem repetir e sem números/medidas (500ML, C/12...)."""
    vistos, saida = set(), []
    for w in re.findall(r"[A-Z0-9]{2,}", _sem_acento(t).upper()):
        if w in _STOP or w.isdigit() or re.fullmatch(r"\d+[A-Z]{1,3}", w) or w in vistos:
            continue
        vistos.add(w)
        saida.append(w)
    return saida


def sugerir_categorias(con):
    """Aprende com os produtos que já têm categoria da loja (nome e grupo) e sugere a categoria
    dos que não têm. A primeira palavra do nome (o tipo do produto) pesa mais que cor, marca e
    tamanho, e a confiança só é alta quando a categoria combina com a maior parte do nome.
    Não altera nada. Devolve (sugestões, quantos produtos serviram de exemplo)."""
    nomes_cat = {r["id"]: r["nome"] for r in con.execute("SELECT id, nome FROM categorias_site")}
    treino = con.execute("SELECT nome, grupo, categoria_id FROM produtos WHERE categoria_id IS NOT NULL").fetchall()
    tok_cat, tok_tot, grupo_cat = collections.defaultdict(collections.Counter), collections.Counter(), collections.defaultdict(collections.Counter)
    for r in treino:
        for t in _tokens_cat(r["nome"]):
            tok_cat[t][r["categoria_id"]] += 1
            tok_tot[t] += 1
        if r["grupo"]:
            grupo_cat[r["grupo"]][r["categoria_id"]] += 1
    n_treino = len(treino)
    saida = []
    if not n_treino:
        return saida, 0
    for p in con.execute("SELECT id, nome, grupo, ean FROM produtos WHERE categoria_id IS NULL AND ativo=1 "
                         "ORDER BY nome COLLATE NOCASE"):
        toks = _tokens_cat(p["nome"])
        pontos = collections.Counter()
        for i, t in enumerate(toks):
            if tok_tot[t] >= 2:
                peso = math.log(1 + n_treino / tok_tot[t]) * (2.5 if i == 0 else 1.0)
                for cat, n in tok_cat[t].items():
                    pontos[cat] += peso * n / tok_tot[t]
        gc = grupo_cat.get(p["grupo"] or "")
        if gc and sum(gc.values()) >= 3:
            total = sum(gc.values())
            for cat, n in gc.items():
                pontos[cat] += 1.5 * n / total
        if not pontos:
            continue
        top = pontos.most_common(3)
        melhor, segundo = top[0][1], (top[1][1] if len(top) > 1 else 0)
        cat = top[0][0]
        cobertura = sum(1 for t in toks if tok_cat[t][cat] > 0) / max(1, len(toks))
        tipo_sabido = bool(toks) and tok_tot[toks[0]] >= 2
        tipo_apoia = bool(toks) and tok_cat[toks[0]][cat] > 0
        if melhor >= 2.5 and melhor >= 1.7 * segundo and cobertura >= 0.5 and tipo_apoia:
            conf = "alta"
        elif melhor >= 1.2 and melhor >= 1.25 * segundo and cobertura >= 0.34 and (tipo_apoia or not tipo_sabido):
            conf = "media"
        else:
            conf = "baixa"
        saida.append({"pid": p["id"], "nome": p["nome"], "grupo": p["grupo"], "confianca": conf,
                      "categoria_id": cat, "categoria": nomes_cat.get(cat, ""),
                      "outras": [{"id": c, "nome": nomes_cat.get(c, "")} for c, _ in top[1:]]})
    return saida, n_treino


def listar_sugestoes(con, conf="alta", q="", pagina=1, por_pagina=40):
    todas, n_treino = sugerir_categorias(con)
    contagem = collections.Counter(x["confianca"] for x in todas)
    termos = (q or "").lower().split()
    itens = [x for x in todas if (conf == "todas" or x["confianca"] == conf)
             and all(t in x["nome"].lower() for t in termos)]
    sem_sug = con.execute("SELECT COUNT(*) FROM produtos WHERE categoria_id IS NULL AND ativo=1").fetchone()[0] - len(todas)
    return {"treino": n_treino, "contagem": dict(contagem), "sem_sugestao": max(0, sem_sug), "total": len(itens),
            "pagina": pagina, "por_pagina": por_pagina,
            "itens": itens[(pagina - 1) * por_pagina: pagina * por_pagina]}


def aplicar_sugestoes(con, itens, quem, conf=""):
    """Grava a categoria nos produtos (só no painel). Com conf, aplica todas daquele nível."""
    if conf:
        itens = [{"pid": x["pid"], "categoria_id": x["categoria_id"]}
                 for x in sugerir_categorias(con)[0] if x["confianca"] == conf]
    cats = {r["id"]: r["nome"] for r in con.execute("SELECT id, nome FROM categorias_site")}
    n = 0
    for i in itens[:5000]:
        pid, cid = int(i["pid"]), str(i.get("categoria_id") or "")
        if cid not in cats:
            continue
        r = con.execute("SELECT categoria_id FROM produtos WHERE id=?", (pid,)).fetchone()
        if not r or r[0] == cid:
            continue
        con.execute("UPDATE produtos SET categoria_id=?, categoria_nome=?, atualizado_em=? WHERE id=?",
                    (cid, cats[cid], agora(), pid))
        con.execute("INSERT INTO historico(produto_id,quando,campo,de,para,origem) VALUES(?,?,?,?,?,?)",
                    (pid, agora(), "categoria", r[0] or "", cats[cid], (quem or "painel") + " (sugestão)"))
        n += 1
    con.commit()
    return {"ok": True, "aplicadas": n}


def _novos_candidatos(con):
    """Produtos que ainda não estão no site e que 'existem de verdade': cadastrados à mão, ou que
    venderam no caixa, ou que chegaram em nota fiscal (a nota e a venda já são a prova)."""
    return con.execute(
        "SELECT p.* FROM produtos p WHERE (p.ativo=1 OR p.ativo IS NULL) AND p.nome NOT IN ('DIVERSOS','ENTREGA') "
        "AND NOT EXISTS (SELECT 1 FROM canais k WHERE k.produto_id=p.id AND k.canal='nuvemshop') "
        "AND (p.origem='manual' "
        "     OR EXISTS (SELECT 1 FROM vendas_gz v WHERE v.gz_codigo=p.gz_codigo AND p.gz_codigo IS NOT NULL AND v.qtd>0) "
        "     OR EXISTS (SELECT 1 FROM de_para d WHERE d.produto_id=p.id AND d.estado='confirmado')) "
        "ORDER BY p.nome").fetchall()


def _motivos_novos(con):
    vend = {r[0]: (r[1] or 0, r[2] or 0) for r in con.execute(
        "SELECT gz_codigo, SUM(valor), SUM(qtd) FROM vendas_gz GROUP BY gz_codigo")}
    ent = {r[0]: r[1] for r in con.execute(
        "SELECT d.produto_id, MAX(n.emissao) FROM de_para d JOIN notas_itens n ON n.cnpj=d.cnpj "
        "AND n.cod_fornecedor=d.cod_fornecedor WHERE d.estado='confirmado' GROUP BY d.produto_id")}
    return vend, ent


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
    vend, ent = _motivos_novos(con)
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
        mot = []
        v = vend.get(str(r["gz_codigo"] or ""))
        if v and v[1] > 0:
            mot.append(f"vendeu no caixa (R$ {v[0]:.0f})")
        if ent.get(r["id"]):
            mot.append(f"chegou na nota de {ent[r['id']][8:10]}/{ent[r['id']][5:7]}")
        if r["origem"] == "manual" and r["estoque"] is not None:
            mot.append("contado na prateleira")
        itens.append({"pid": r["id"], "sku": r["sku"] or "", "motivos": mot, "vendas_valor": (v[0] if v else 0),
                      "nome": r["nome"], "ean": ean, "preco": r["preco"],
                      "estoque": r["estoque"], "grupo": r["grupo"], "categoria_id": cid,
                      "categoria": nomes_cat.get(cid, ""), "faltas": faltas,
                      "foto": f"/api/foto/{r['id']}" if os.path.exists(os.path.join(fotos_dir(), f"{r['id']}.jpg")) else None})
    itens.sort(key=lambda i: (-(i["vendas_valor"] or 0), i["nome"]))      # o que mais vende vem primeiro
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
                    if i.get("sku"):
                        variante["sku"] = str(i["sku"])
                else:
                    variante["sku"] = str(i.get("sku") or f"PAINEL-{i['pid']}")   # para o painel reconhecer o produto depois
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
DESTINOS_ENVIO = {"anota": "Anota Aí", "ifood": "iFood", "n99": "99Food", "site": "Site (Nuvemshop)"}
for _d in DESTINOS_ENVIO:   # contados depois da última vez que você marcou "já enviei" para esse destino
    FILTROS["novo_" + _d] = ("p.estoque IS NOT NULL AND COALESCE(p.conferido_em,'') > "
                             f"COALESCE((SELECT valor FROM meta WHERE chave='envio_{_d}_em'),'')")
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
         "nuvemshop_configurada": bool(carregar_config()["token"]),
         "pedidos": {"lidos_em": (con.execute("SELECT valor FROM meta WHERE chave='pedidos_lidos_em'").fetchone() or [None])[0],
                     "total": um("SELECT COUNT(*) FROM pedidos_site")}}
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
            try:
                f = float(str(novo).replace(",", "."))
            except ValueError:
                raise ValueError("A quantidade precisa ser um número.") from None
            if f != f or f in (float("inf"), float("-inf")) or f != int(f):
                raise ValueError("A quantidade precisa ser um número inteiro (sem vírgula).")
            novo = int(f)
            if novo < 0:
                raise ValueError("O estoque não pode ser negativo.")
            if novo > 1_000_000:
                raise ValueError("Quantidade grande demais. Confira o número.")
        con.execute("UPDATE produtos SET estoque=?, conferido_em=?, atualizado_em=? WHERE id=?",
                    (novo, agora() if novo is not None else None, agora(), pid))
        con.execute("INSERT INTO historico(produto_id,quando,campo,de,para,origem) "
                    "VALUES(?,?,?,?,?,?)",
                    (pid, agora(), "estoque",
                     "" if p["estoque"] is None else str(p["estoque"]),
                     "" if novo is None else str(novo), quem))
    mudar = {}
    for campo_if in ("ifood_depto", "ifood_cat"):       # categoria que vai ao iFood (não muda o grupo do Gestor Z)
        if campo_if in dados:
            mudar[campo_if] = " ".join(str(dados[campo_if] or "").split())[:60] or None
    if "nome" in dados:
        nome = " ".join(str(dados["nome"] or "").split())
        if not 2 <= len(nome) <= 150:
            raise ValueError("O nome precisa ter de 2 a 150 letras.")
        mudar["nome"] = nome
    for campo in ("preco", "custo"):
        if campo in dados:
            bruto = str(dados[campo] if dados[campo] is not None else "").strip()
            v = None if bruto == "" else num(bruto)
            if bruto != "" and v is None:
                raise ValueError("Preço inválido. Use só números, por exemplo 12,50.")
            if v is not None and not 0 <= v <= 100000:   # também recusa NaN e infinito
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


# ------------------------------------------------------------- cruzamento de preços (Gestor Z manda)

TOLERANCIA_PRECO = 0.05
TIPOS_CRUZAMENTO = {
    "painel": "Painel diferente do Gestor Z",
    "site": "Site (Nuvemshop) diferente",
    "ifood": "iFood diferente",
    "n99": "99Food diferente",
    "sem_oficial": "Sem preço oficial no Gestor Z",
}


def cruzamento_precos(con, tipo="", divisor=0.70, limite=400):
    """Compara o preço oficial (Gestor Z) com o painel, o site, o catálogo do iFood e o da 99Food.
    Site = preço do Gestor Z. iFood e 99Food = preço do Gestor Z ÷ 0,70. Só leitura."""
    ifood = {norm_ean(r["ean"]): r["preco"] for r in con.execute("SELECT ean, preco FROM ifood_catalogo") if norm_ean(r["ean"])}
    n99 = {norm_ean(r["ean"]): r["preco"] for r in con.execute("SELECT ean, preco FROM n99_catalogo") if norm_ean(r["ean"])}
    site = {}
    for r in con.execute("SELECT produto_id, preco FROM canais WHERE canal='nuvemshop'"):
        site.setdefault(r["produto_id"], []).append(r["preco"])
    contagens = {k: 0 for k in TIPOS_CRUZAMENTO}
    linhas = []
    dif = lambda atual, esperado: atual is not None and esperado is not None and abs(atual - esperado) > TOLERANCIA_PRECO
    for p in con.execute("SELECT * FROM produtos ORDER BY nome COLLATE NOCASE"):
        ean = norm_ean(p["ean"])
        oficial = p["preco_gz"] if p["preco_gz"] and p["preco_gz"] > 0 else None
        esp_ap = round(oficial / divisor + 1e-9, 2) if oficial else None
        pi, p9 = ifood.get(ean) if ean else None, n99.get(ean) if ean else None
        ps = site.get(p["id"])
        ps = ps[0] if ps and len(ps) == 1 else None
        painel = p["preco"] if p["preco"] and p["preco"] > 0 else None
        em_algum = p["estoque"] is not None or pi is not None or p9 is not None or ps is not None or p["id"] in site
        flags = []
        if oficial is None:
            if em_algum:
                flags.append("sem_oficial")
        else:
            if painel is None or dif(painel, oficial):
                flags.append("painel")
            if dif(ps, oficial):
                flags.append("site")
            if dif(pi, esp_ap):
                flags.append("ifood")
            if dif(p9, esp_ap):
                flags.append("n99")
        for f in flags:
            contagens[f] += 1
        if flags and (not tipo or tipo in flags):
            linhas.append({"id": p["id"], "nome": p["nome"], "ean": p["ean"], "contado": p["estoque"], "oficial": oficial,
                           "esperado_apps": esp_ap, "painel": painel, "site": ps, "ifood": pi, "n99": p9, "flags": flags})
    meta = {k: (con.execute("SELECT valor FROM meta WHERE chave=?", (k,)).fetchone() or [None])[0]
            for k in ("gestorz_importado_em", "nuvemshop_sync_em", "ifood_catalogo_em", "n99_catalogo_em")}
    return {"contagens": contagens, "tipos": TIPOS_CRUZAMENTO, "itens": linhas[:limite], "total": len(linhas),
            "cortou": len(linhas) > limite, "tolerancia": TOLERANCIA_PRECO, "divisor": divisor, "quando": meta}


_TAM_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(ml|g|gr|kg|l|lt|un|und|unid|unidades|cm|mm|m|pcs|pc|pecas)\b")
_SEM_PESO = {"de", "da", "do", "das", "dos", "e", "com", "para", "a", "o", "em", "c", "p"}


def _tokens_nome(nome):
    t = re.sub(r"[^a-z0-9., ]+", " ", _sem_acento(nome).lower())
    tam = set()
    for n, u in _TAM_RE.findall(t):
        u = {"gr": "g", "lt": "l", "und": "un", "unid": "un", "unidades": "un", "pcs": "pc", "pecas": "pc"}.get(u, u)
        tam.add(n.replace(",", ".").rstrip("0").rstrip(".") + u)
    palavras = {(w[:-1] if len(w) > 4 and w.endswith("s") else w)       # johnsons = johnson
                for w in re.sub(r"[.,]", " ", _TAM_RE.sub(" ", t)).split() if w not in _SEM_PESO and len(w) > 1}
    return palavras, tam


def sugestoes_em_massa(con, limite=300):
    """Para cada produto sem preço oficial, o produto do Gestor Z de nome mais parecido (mesmas palavras e mesmo
    tamanho). Só leitura. 'auto' = parecido o bastante para vir marcado; o resto você confere."""
    ids = [i["id"] for i in cruzamento_precos(con, "sem_oficial", limite=limite)["itens"]]
    base = []
    for r in con.execute("SELECT id, nome, preco_gz, ean FROM produtos WHERE origem='gestorz' AND preco_gz > 0"):
        pal, tam = _tokens_nome(r["nome"])
        if pal:
            base.append((r, pal, tam))
    saida = []
    for pid in ids:
        p = con.execute("SELECT id, nome, ean, estoque FROM produtos WHERE id=?", (pid,)).fetchone()
        pal, tam = _tokens_nome(p["nome"])
        melhor = None
        for r, pal2, tam2 in base:
            if not pal or not pal2:
                continue
            inter = len(pal & pal2)
            if not inter:
                continue
            jac = inter / len(pal | pal2)
            igual = (tam == tam2) if (tam and tam2) else None
            sc = jac - (0.35 if igual is False else 0)
            if melhor is None or sc > melhor[0]:
                melhor = (sc, jac, igual, r)
        item = {"id": p["id"], "nome": p["nome"], "ean": p["ean"], "contado": p["estoque"], "sugestao": None}
        if melhor and melhor[0] >= 0.4:
            sc, jac, igual, r = melhor
            item["sugestao"] = {"id": r["id"], "nome": r["nome"], "preco": r["preco_gz"], "ean": r["ean"],
                                "parecido": round(jac, 2), "tamanho_igual": igual,
                                "auto": jac >= 0.8 and igual is not False and (igual is True or bool(tam) is False)}
        saida.append(item)
    saida.sort(key=lambda x: -(x["sugestao"]["parecido"] if x["sugestao"] else 0))
    return {"itens": saida}


def aplicar_sugestoes(con, pares, quem="painel"):
    """Aplica o preço do Gestor Z de um produto parecido (par = [id do seu produto, id do produto do Gestor Z]).
    Grava o preço no painel como preço oficial. Não envia nada às lojas."""
    feitos = 0
    for par in pares[:500]:
        pid, ref = int(par[0]), int(par[1])
        r = con.execute("SELECT nome, preco_gz FROM produtos WHERE id=? AND origem='gestorz' AND preco_gz > 0", (ref,)).fetchone()
        p = con.execute("SELECT id, preco FROM produtos WHERE id=?", (pid,)).fetchone()
        if not r or not p:
            continue
        con.execute("UPDATE produtos SET preco=?, preco_gz=?, atualizado_em=? WHERE id=?", (r["preco_gz"], r["preco_gz"], agora(), pid))
        con.execute("INSERT INTO historico(produto_id,quando,campo,de,para,origem) VALUES(?,?,?,?,?,?)",
                    (pid, agora(), "preco", "" if p["preco"] is None else f"{p['preco']:.2f}", f"{r['preco_gz']:.2f}",
                     f"{quem} (preço do Gestor Z: {r['nome'][:40]})"))
        feitos += 1
    con.commit()
    return {"aplicados": feitos}


def definir_preco_oficial(con, pares, quem="painel"):
    """Define à mão o preço oficial de produtos que não têm preço no Gestor Z (par = [id, preço]).
    Grava no painel (preço e preço oficial). Não envia nada às lojas."""
    feitos = 0
    for par in pares[:500]:
        pid = int(par[0])
        try:
            preco = round(float(str(par[1]).replace(",", ".")), 2)
        except ValueError:
            raise ValueError("Preço inválido. Use só números, por exemplo 12,50.") from None
        if not 0 < preco <= 100000:
            raise ValueError("Preço fora do normal.")
        p = con.execute("SELECT id, preco FROM produtos WHERE id=?", (pid,)).fetchone()
        if not p:
            continue
        con.execute("UPDATE produtos SET preco=?, preco_gz=?, atualizado_em=? WHERE id=?", (preco, preco, agora(), pid))
        con.execute("INSERT INTO historico(produto_id,quando,campo,de,para,origem) VALUES(?,?,?,?,?,?)",
                    (pid, agora(), "preco", "" if p["preco"] is None else f"{p['preco']:.2f}", f"{preco:.2f}", quem + " (preço oficial definido à mão)"))
        feitos += 1
    con.commit()
    return {"aplicados": feitos}


def aplicar_preco_oficial(con, ids, quem="painel"):
    """Põe no painel o preço oficial do Gestor Z nos produtos marcados. Não envia nada às lojas."""
    feitos = 0
    for pid in [int(i) for i in ids][:500]:
        p = con.execute("SELECT id, preco, preco_gz FROM produtos WHERE id=?", (pid,)).fetchone()
        if not p or not p["preco_gz"] or p["preco_gz"] <= 0:
            continue
        if p["preco"] is not None and abs(p["preco"] - p["preco_gz"]) <= 0.004:
            continue
        con.execute("UPDATE produtos SET preco=?, atualizado_em=? WHERE id=?", (p["preco_gz"], agora(), pid))
        con.execute("INSERT INTO historico(produto_id,quando,campo,de,para,origem) VALUES(?,?,?,?,?,?)",
                    (pid, agora(), "preco", "" if p["preco"] is None else f"{p['preco']:.2f}", f"{p['preco_gz']:.2f}", quem + " (preço oficial)"))
        feitos += 1
    con.commit()
    return {"aplicados": feitos}


def sugestoes_preco(con, pid):
    """Produto sem preço: procura no cadastro do Gestor Z (a tabela oficial de preços) os produtos de nome mais
    parecido, para você escolher. Só leitura; quem grava o preço é você, no botão da sugestão."""
    p = con.execute("SELECT id, nome, ean_norm FROM produtos WHERE id=?", (pid,)).fetchone()
    if not p:
        raise ValueError("Produto não encontrado.")
    limpa = lambda t: re.sub(r"[^a-z0-9 ]+", " ", _sem_acento(t).lower()).split()
    alvo = limpa(p["nome"])
    if not alvo:
        return []
    achados = []
    for r in con.execute("SELECT id, nome, preco, ean FROM produtos WHERE origem='gestorz' AND preco > 0 AND id<>?", (pid,)):
        outro = limpa(r["nome"])
        if not outro:
            continue
        comum = len(set(alvo) & set(outro)) / max(1, len(set(alvo) | set(outro)))
        if comum < 0.34:
            continue
        sim = difflib.SequenceMatcher(None, " ".join(alvo), " ".join(outro)).ratio()
        achados.append((0.5 * comum + 0.5 * sim, r))
    achados.sort(key=lambda x: -x[0])
    return [{"id": r["id"], "nome": r["nome"], "preco": r["preco"], "ean": r["ean"], "parecido": round(sc, 2)} for sc, r in achados[:4]]


def desfazer_contagem(con, pid, quem="painel"):
    """Desfaz a ÚLTIMA contagem de um produto (volta ao valor e à data anteriores).
    Só desfaz se o estoque ainda é o que essa contagem gravou. Fica só no painel."""
    p = con.execute("SELECT * FROM produtos WHERE id=?", (pid,)).fetchone()
    if not p:
        raise ValueError("Produto não encontrado.")
    ult = con.execute("SELECT * FROM historico WHERE produto_id=? AND campo='estoque' "
                      "ORDER BY id DESC LIMIT 1", (pid,)).fetchone()
    if not ult:
        raise ValueError("Este produto não tem contagem para desfazer.")
    atual = "" if p["estoque"] is None else str(p["estoque"])
    if atual != (ult["para"] or ""):
        raise ValueError("O estoque deste produto mudou depois dessa contagem. Não dá para desfazer sozinho; corrija o número.")
    antes = ult["de"] if ult["de"] not in (None, "") else None
    novo = int(float(antes)) if antes is not None else None
    ant = con.execute("SELECT quando FROM historico WHERE produto_id=? AND campo='estoque' AND id<? "
                      "ORDER BY id DESC LIMIT 1", (pid, ult["id"])).fetchone()
    quando_ant = ant["quando"] if (ant and novo is not None) else None
    con.execute("UPDATE produtos SET estoque=?, conferido_em=?, atualizado_em=? WHERE id=?",
                (novo, quando_ant, agora(), pid))
    con.execute("DELETE FROM historico WHERE id=?", (ult["id"],))
    con.execute("INSERT INTO historico(produto_id,quando,campo,de,para,origem) VALUES(?,?,?,?,?,?)",
                (pid, agora(), "estoque_desfeito", ult["para"] or "", "" if novo is None else str(novo), quem))
    con.commit()
    recalcular(con, [pid])
    return detalhe(con, pid)


# ------------------------------------------------------------- planilha do iFood

MODELO_IFOOD = os.path.join(STATIC, "modelos", "ifood_planilha_modelo.xlsx")
LINHAS_POR_PLANILHA = 999          # o modelo do iFood tem 999 linhas de dados


def ler_xlsx(raw, exigir=None):
    """Primeira aba de um .xlsx como lista de linhas (só a biblioteca padrão).
    Com `exigir` (lista de grupos de títulos aceitos), devolve a primeira aba que tem esses títulos."""
    z = zipfile.ZipFile(io.BytesIO(raw))
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    textos = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(ns + "si"):
            textos.append("".join(t.text or "" for t in si.iter(ns + "t")))
    abas = sorted(n for n in z.namelist() if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"))
    if not abas:
        raise ValueError("Isso não parece uma planilha .xlsx.")
    def ler_aba(caminho):
        linhas = []
        for row in ET.fromstring(z.read(caminho)).iter(ns + "row"):
            d = {}
            for c in row.findall(ns + "c"):
                letras = re.match(r"[^0-9]+", c.get("r") or "A").group(0)   # tolera colunas além do Z mal numeradas, como "{1"
                col = 0
                for ch in letras:
                    col = col * 26 + ord(ch) - 64
                v, t = c.find(ns + "v"), c.get("t")
                if t == "s" and v is not None:
                    val = textos[int(v.text)]
                elif t == "inlineStr":
                    val = "".join(x.text or "" for x in c.iter(ns + "t"))
                else:
                    val = v.text if v is not None else None
                d[col - 1] = val
            linhas.append([d.get(i) for i in range(max(d) + 1)] if d else [])
        return linhas

    if exigir:
        for caminho in abas:
            try:
                linhas = ler_aba(caminho)
            except KeyError:
                continue
            cab = [_cab(c) for c in (linhas[0] if linhas else [])]
            if all(any(o in cab for o in grupo) for grupo in exigir):
                return linhas
    return ler_aba(abas[0])


def importar_ifood_catalogo(con, raw, quem="painel"):
    linhas = ler_xlsx(raw)
    if not linhas:
        raise ValueError("A planilha está vazia.")
    cab = [_cab(c) for c in linhas[0]]
    ix = lambda *nomes: next((cab.index(n) for n in nomes if n in cab), None)
    ie, inm, iv = ix("EAN DO PRODUTO", "EAN"), ix("NOME DO PRODUTO", "NOME"), ix("EM VENDA")
    ib, ip, iq, idt = ix("MOTIVO BLOQUEIO REGRAS"), ix("PRECO INTEGRACAO"), ix("QTD ESTOQUE INTEGRACAO"), ix("DT")
    if ie is None or iv is None:
        raise ValueError("Não achei as colunas ean_do_produto e em_venda. É o catálogo exportado do portal do iFood?")
    g = lambda l, i: (l[i] if i is not None and i < len(l) else None)
    con.execute("DELETE FROM ifood_catalogo")
    n = ruptura = em_venda = 0
    for l in linhas[1:]:
        ean = "".join(c for c in str(g(l, ie) or "") if c.isdigit())
        if not ean:
            continue
        bloqueio = str(g(l, ib) or "")
        venda = 1 if str(g(l, iv)).strip().lower() == "true" else 0
        con.execute("INSERT OR REPLACE INTO ifood_catalogo(ean,nome,em_venda,bloqueio,preco,qtd,dt) VALUES(?,?,?,?,?,?,?)",
                    (ean, g(l, inm), venda, bloqueio, num(g(l, ip)), num(g(l, iq)), str(g(l, idt) or "")))
        n += 1
        ruptura += "Ruptura" in bloqueio
        em_venda += venda
    set_meta(con, "ifood_catalogo_em", agora())
    con.commit()
    return {"itens": n, "em_venda": em_venda, "ruptura": ruptura}


def _ean_ifood(v):
    d = "".join(c for c in str(v or "") if c.isdigit())
    return d if 8 <= len(d) <= 14 and not d.startswith("2") else ""     # 2xxxx são códigos internos do caixa


def marcar_envio(con, destino):
    """Anota que você acabou de subir os contados para esse destino. Depois disso, só o que for contado
    de novo aparece como 'ainda não enviado'. Não envia nada: só guarda a data."""
    if destino not in DESTINOS_ENVIO:
        raise ValueError("Destino desconhecido.")
    set_meta(con, f"envio_{destino}_em", agora())
    con.commit()
    return {"ok": True, "destino": destino, "em": agora()}


def planilha_ifood(con, divisor=0.70, so_novos=None):
    """Produtos conferidos ou cadastrados no painel, prontos para a planilha de importação do iFood.
    Preço = preço do painel / divisor. Estoque = estimado (ou contagem). O iFood atualiza preço e estoque de
    quem já existe, cadastra sozinho quem não existe e destrava quem estava em ruptura quando a planilha traz
    o estoque atual: por isso item em ruptura também vai, com o estoque do painel."""
    if not 0.05 <= divisor <= 1.0:
        raise ValueError("O divisor do preço precisa estar entre 0,05 e 1.")
    catalogo = {r["ean"]: dict(r) for r in con.execute("SELECT * FROM ifood_catalogo")}
    itens, falta, vistos = [], collections.defaultdict(list), set()
    consulta, args = "SELECT * FROM produtos WHERE (estoque IS NOT NULL OR origem='manual')", []
    if so_novos in DESTINOS_ENVIO:      # só o contado depois do último envio marcado para esse destino
        ult = (con.execute("SELECT valor FROM meta WHERE chave=?", (f"envio_{so_novos}_em",)).fetchone() or [""])[0] or ""
        consulta += " AND COALESCE(conferido_em,'') > ?"
        args.append(ult)
    for p in con.execute(consulta + " ORDER BY nome COLLATE NOCASE", args):
        ean = _ean_ifood(p["ean"])
        if not ean:
            falta["sem_codigo"].append(p["nome"])
            continue
        if ean in vistos:
            continue
        vistos.add(ean)
        pe = preco_efetivo(p)
        if not pe or pe <= 0:
            falta["sem_preco"].append(p["nome"])
            continue
        if p["estoque"] is None:
            falta["sem_contagem"].append(p["nome"])
            continue
        base = p["estimado"] if p["estimado"] is not None else p["estoque"]
        qtd = max(0, int(math.floor(base)))
        cat = catalogo.get(ean)
        ruptura = bool(cat and "Ruptura" in (cat["bloqueio"] or ""))
        preco = round(pe / divisor + 1e-9, 2)
        itens.append({"ean": int(ean), "nome": p["nome"], "preco": preco, "qtd": qtd, "ativo": 1 if qtd > 0 else 0,
                      "no_ifood": bool(cat), "ruptura": ruptura, "caixa": pe})
    enviados = {str(i["ean"]) for i in itens}
    for e, cat in catalogo.items():              # em ruptura no iFood e sem contagem: não dá para mandar estoque
        if "Ruptura" in (cat["bloqueio"] or "") and e not in enviados:
            falta["ruptura_sem_contagem"].append(cat["nome"] or e)
    novos = sum(1 for i in itens if not i["no_ifood"])
    return {"divisor": divisor, "total": len(itens), "no_ifood": len(itens) - novos, "novos_no_ifood": novos,
            "zerados": sum(1 for i in itens if i["qtd"] == 0),
            "ruptura": sum(1 for i in itens if i["ruptura"] and i["qtd"] > 0),
            "catalogo_lido": bool(catalogo), "arquivos": max(1, math.ceil(len(itens) / LINHAS_POR_PLANILHA)),
            "faltas": {k: {"n": len(v), "nomes": v[:8]} for k, v in falta.items()},
            "revisar": [], "itens": itens}


CANAIS_IFOOD = {"app": "App", "shop": "Shop", "white": "White Label"}


def _canal_ifood(n, canal):
    """Texto da coluna Canal como o modelo aceita (nas linhas 9 a 41 a lista do modelo usa 'Ifood')."""
    if canal == "white":
        return "White Label"
    return ("Ifood " if 9 <= n <= 41 else "iFood ") + CANAIS_IFOOD.get(canal, "Shop")


def xlsx_ifood(itens, canal="shop"):
    """Preenche o modelo do iFood (até 999 produtos) sem mexer no resto da planilha."""
    if len(itens) > LINHAS_POR_PLANILHA:
        raise ValueError("O modelo do iFood aceita até %d produtos por planilha." % LINHAS_POR_PLANILHA)
    with open(MODELO_IFOOD, "rb") as f:
        origem = zipfile.ZipFile(io.BytesIO(f.read()))
    xml = origem.read("xl/worksheets/sheet1.xml").decode("utf-8")
    num_ = lambda v: repr(float(v)).rstrip("0").rstrip(".") if isinstance(v, float) else str(v)
    for n, i in enumerate(itens, start=2):
        for col, valor in (("A", i["ean"]), ("C", i["preco"]), ("D", i["qtd"]), ("E", i["ativo"])):
            xml, k = re.subn(rf'<c r="{col}{n}" s="(\d+)"/>', rf'<c r="{col}{n}" s="\1"><v>{num_(valor)}</v></c>', xml)
            if not k:
                raise ValueError("O modelo do iFood mudou de formato; envie o modelo atualizado.")
        xml = re.sub(rf'<c r="J{n}" s="(\d+)"/>',
                     lambda m: f'<c r="J{n}" s="{m.group(1)}" t="inlineStr"><is><t>{_canal_ifood(n, canal)}</t></is></c>', xml)
        xml = re.sub(rf'<c r="B{n}" s="(\d+)"/>',
                     lambda m: f'<c r="B{n}" s="{m.group(1)}" t="inlineStr"><is><t>{xml_escape(i["nome"])}</t></is></c>', xml)
    saida = io.BytesIO()
    with zipfile.ZipFile(saida, "w", zipfile.ZIP_DEFLATED) as z:
        for item in origem.infolist():
            z.writestr(item, xml.encode("utf-8") if item.filename == "xl/worksheets/sheet1.xml" else origem.read(item.filename))
    return saida.getvalue()


def revisar_ifood_csv(plano):
    saida = io.StringIO()
    w = csv.writer(saida, delimiter=";")
    w.writerow(["situacao", "produto", "codigo_de_barras", "contagem_painel", "estimado_painel", "enviado_ao_ifood", "detalhe"])
    for k, rot in (("ruptura_sem_contagem", "em ruptura no iFood e sem contagem no painel: conte para voltar a vender"),
                   ("sem_preco", "sem preço no painel"), ("sem_codigo", "sem código de barras válido"), ("sem_contagem", "sem contagem")):
        for nome in plano["faltas"].get(k, {}).get("nomes", []):
            w.writerow(["ficou de fora", nome, "", "", "", "", rot])
    return "\ufeff" + saida.getvalue()


# ----------------------------------------------------------------- planilhas da 99Food

MODELO_99_EDITAR = os.path.join(STATIC, "modelos", "n99_editar_modelo.xlsx")
MODELO_99_ADICIONAR = os.path.join(STATIC, "modelos", "n99_adicionar_modelo.xlsx")
MAX_99 = 3000                      # limite de itens por planilha no portal da 99


def importar_n99_catalogo(con, raw, quem="painel"):
    linhas = ler_xlsx(raw)
    cab = next((i for i, l in enumerate(linhas[:4]) if l and str(l[0] or "").upper().startswith("ID DO ITEM")), None)
    if cab is None or len(linhas[cab]) < 9:
        raise ValueError("Não achei as colunas da 99Food. Use “Baixar dados de todos os itens” no portal da 99.")
    con.execute("DELETE FROM n99_catalogo")
    g = lambda l, i: str(l[i]).strip() if i < len(l) and l[i] is not None else ""
    n = ativos = 0
    for l in linhas[cab + 1:]:
        if not g(l, 0):
            continue
        preco_txt = g(l, 5)
        try:
            preco = num_br(preco_txt.replace("R$", ""))
        except ValueError:
            preco = None
        try:
            qtd = num_br(g(l, 6))
        except ValueError:
            qtd = 0.0
        con.execute("INSERT OR REPLACE INTO n99_catalogo VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (g(l, 0), g(l, 1), g(l, 2), g(l, 3), g(l, 4), preco, preco_txt, qtd, g(l, 7), g(l, 8),
                     g(l, 9), g(l, 10), g(l, 11), g(l, 12), g(l, 13)))
        n += 1
        ativos += qtd > 0 and g(l, 8).lower().startswith("dispon")
    set_meta(con, "n99_catalogo_em", agora())
    con.commit()
    return {"itens": n, "ativos_com_estoque": ativos}


def _chaves_ean(v):
    """O código e, se for GTIN-14 (caixa), também o EAN-13 do produto dentro dela."""
    d = norm_ean(v)
    chaves = {d} if d else set()
    if len(d) == 14:
        doze = d[1:13]
        chaves.add(norm_ean(doze + _digito_ean13(doze)))
    return chaves


def _classificar_nome(treino, nome):
    """Categoria da 99 por semelhança de nome com os itens que já estão lá."""
    tok_cat, tok_tot = collections.defaultdict(collections.Counter), collections.Counter()
    for n, c in treino:
        for t in _tokens_cat(n):
            tok_cat[t][c] += 1
            tok_tot[t] += 1
    pontos = collections.Counter()
    for i, t in enumerate(_tokens_cat(nome)):
        if tok_tot[t] >= 2:
            peso = math.log(1 + len(treino) / tok_tot[t]) * (2.5 if i == 0 else 1.0)
            for c, n in tok_cat[t].items():
                pontos[c] += peso * n / tok_tot[t]
    if not pontos:
        return None, "baixa"
    top = pontos.most_common(2)
    segundo = top[1][1] if len(top) > 1 else 0
    return top[0][0], ("alta" if top[0][1] >= 2.5 and top[0][1] >= 1.7 * segundo else "media" if top[0][1] >= 1.2 else "baixa")


def plano_99(con, divisor=0.70):
    """O que mudar na 99Food. Base = iFood: item fora de venda ou em ruptura no iFood fica com estoque 0;
    item que o painel conferiu usa o mesmo estoque e o mesmo preço (÷ divisor) do iFood."""
    cat99 = [dict(r) for r in con.execute("SELECT * FROM n99_catalogo")]
    if not cat99:
        raise ValueError("Leia primeiro o catálogo da 99Food (Baixar dados de todos os itens).")
    painel = {}
    for i in planilha_ifood(con, divisor)["itens"]:
        for k in _chaves_ean(i["ean"]):
            painel[k] = i
    ifood = {}
    for r in con.execute("SELECT * FROM ifood_catalogo"):
        for k in _chaves_ean(r["ean"]):
            ifood[k] = dict(r)
    editar, conferir, vistos = [], [], set()
    zerar = atualizar = sem_mudar = 0
    for r in cat99:
        chaves = _chaves_ean(r["ean"])
        p = next((painel[k] for k in chaves if k in painel), None)
        f = next((ifood[k] for k in chaves if k in ifood), None)
        vistos |= chaves
        novo_qtd = novo_preco = motivo = None
        if p:
            novo_qtd, novo_preco = float(p["qtd"]), p["preco"]
            motivo = "estoque e preço do painel" + (" (destrava a ruptura do iFood)" if p["ruptura"] else "")
        elif f:
            if (not f["em_venda"] or "Ruptura" in (f["bloqueio"] or "")) and r["qtd"] > 0:
                novo_qtd, motivo = 0.0, "fora de venda ou em ruptura no iFood e sem contagem no painel"
        elif r["qtd"] > 0 and r["disp"].lower().startswith("dispon"):
            conferir.append({"nome": r["nome"], "ean": r["ean"], "qtd": r["qtd"], "motivo": "ativo na 99, mas não está no iFood nem no painel"})
        mudou_q = novo_qtd is not None and novo_qtd != r["qtd"]
        mudou_p = novo_preco is not None and (r["preco"] is None or abs(novo_preco - r["preco"]) > 0.004)
        if mudou_q or mudou_p:
            linha = dict(r)
            if mudou_q:
                linha["qtd"] = novo_qtd
            if mudou_p:
                linha["preco_txt"] = "R$" + f"{novo_preco:.2f}".replace(".", ",")
            linha["motivo"], linha["zerou"] = motivo, bool(mudou_q and novo_qtd == 0 and r["qtd"] > 0)
            editar.append(linha)
            zerar += linha["zerou"]
            atualizar += not linha["zerou"]
        else:
            sem_mudar += 1
    treino = [(r["nome"], r["categoria"]) for r in cat99 if r["categoria"]]
    novos = []
    for i in planilha_ifood(con, divisor)["itens"]:
        if _chaves_ean(i["ean"]) & vistos:
            continue
        cat, conf = _classificar_nome(treino, i["nome"])
        novos.append({"ean": str(i["ean"]), "nome": i["nome"], "preco": i["preco"], "qtd": i["qtd"], "categoria": cat or "", "confianca": conf})
    return {"divisor": divisor, "itens_99": len(cat99), "ativos_com_estoque": sum(1 for r in cat99 if r["qtd"] > 0),
            "zerar": zerar, "atualizar": atualizar, "sem_mudar": sem_mudar, "conferir": len(conferir),
            "novos": len(novos), "novos_incertos": sum(1 for n in novos if n["confianca"] == "baixa"),
            "editar": editar, "lista_conferir": conferir, "lista_novos": novos}


def _xlsx_preencher(modelo, linhas, colunas, extra_dimensao):
    """Escreve linhas (listas de valores) a partir da linha 3 do modelo da 99, sem tocar no resto."""
    with open(modelo, "rb") as f:
        origem = zipfile.ZipFile(io.BytesIO(f.read()))
    xml = origem.read("xl/worksheets/sheet1.xml").decode("utf-8")
    letras = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    partes = []
    for n, valores in enumerate(linhas, start=3):
        cel = []
        for j, v in enumerate(valores[:colunas]):
            if v is None or v == "":
                continue
            ref = f"{letras[j]}{n}"
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                cel.append(f'<c r="{ref}"><v>{v:g}</v></c>' if isinstance(v, float) else f'<c r="{ref}"><v>{v}</v></c>')
            else:
                cel.append(f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{xml_escape(str(v))}</t></is></c>')
        partes.append(f'<row r="{n}">' + "".join(cel) + "</row>")
    xml = re.sub(r"<row r=\"3\".*?</sheetData>", lambda m: "".join(partes) + "</sheetData>", xml, count=1, flags=re.S)
    xml = re.sub(r'<dimension ref="[^"]*"/>', f'<dimension ref="A1:{letras[colunas - 1]}{max(2, len(linhas) + 2)}"/>', xml, count=1)
    saida = io.BytesIO()
    with zipfile.ZipFile(saida, "w", zipfile.ZIP_DEFLATED) as z:
        for item in origem.infolist():
            z.writestr(item, xml.encode("utf-8") if item.filename == "xl/worksheets/sheet1.xml" else origem.read(item.filename))
    return saida.getvalue()


def _preco_99(r):
    """Preço como número: a 99 recusa o texto 'R$15,57' ao enviar a planilha (Conteúdo inválido em Preço do item)."""
    if r.get("preco_txt") and r["preco_txt"].startswith("R$"):
        try:
            return round(num_br(r["preco_txt"].replace("R$", "")), 2)
        except ValueError:
            pass
    return round(r["preco"], 2) if r.get("preco") is not None else ""


def xlsx_99_editar(plano, teste=False):
    itens = plano["editar"][:5] if teste else plano["editar"]
    if len(itens) > MAX_99:
        raise ValueError("A 99 aceita até %d itens por planilha." % MAX_99)
    linhas = [[r["id"], r["ean"], r["nome"], r["descricao"], r["id_ext"], _preco_99(r), r["qtd"], r["categoria"],
               r["disp"], r["tags"], r["peso"], r["volume"], r["armaz"], r["embal"]] for r in itens]
    return _xlsx_preencher(MODELO_99_EDITAR, linhas, 14, True)


def xlsx_99_adicionar(plano, teste=False):
    itens = plano["lista_novos"][:5] if teste else plano["lista_novos"]
    if len(itens) > MAX_99:
        raise ValueError("A 99 aceita até %d itens por planilha." % MAX_99)
    linhas = [[n["ean"], n["nome"], "", "", round(n["preco"], 2), n["qtd"], n["categoria"]] for n in itens]
    return _xlsx_preencher(MODELO_99_ADICIONAR, linhas, 12, True)


def revisar_99_csv(plano):
    saida = io.StringIO()
    w = csv.writer(saida, delimiter=";")
    w.writerow(["situacao", "produto", "codigo_de_barras", "estoque_atual_99", "detalhe"])
    for r in plano["lista_conferir"]:
        w.writerow(["conferir", r["nome"], r["ean"], r["qtd"], r["motivo"]])
    for n in plano["lista_novos"]:
        if n["confianca"] == "baixa":
            w.writerow(["categoria incerta (novo item)", n["nome"], n["ean"], "", "escolha a categoria na planilha antes de subir"])
    return "\ufeff" + saida.getvalue()


# ------------------------------------------------------ pedidos do site (só leitura)

def sync_pedidos(con, progresso=None):
    """Lê da Nuvemshop os pedidos novos para o estoque estimado descontar as vendas do site."""
    cfg = _exigir_nuvemshop()
    primeira = con.execute("SELECT MIN(substr(conferido_em,1,10)) FROM produtos WHERE estoque IS NOT NULL").fetchone()[0]
    ate = con.execute("SELECT valor FROM meta WHERE chave='pedidos_ate'").fetchone()
    desde = ate[0] if ate else (primeira or datetime.now().strftime("%Y-%m-%d"))
    desde = (datetime.strptime(desde, "%Y-%m-%d") - timedelta(days=2)).strftime("%Y-%m-%d")   # folga: pedidos que mudam
    pagina = lidos = 0
    while True:
        if progresso:
            progresso(f"Lendo pedidos do site desde {desde}: {lidos} até agora")
        try:
            lote = _nuvem(cfg, "GET", f"/orders?created_at_min={desde}T00:00:00-03:00&per_page=200&page={pagina + 1}")
        except RuntimeError as e:
            if " 404" in str(e):
                break                                  # a Nuvemshop responde 404 depois da última página
            raise
        if not isinstance(lote, list) or not lote:
            break
        for o in lote:
            con.execute("INSERT OR REPLACE INTO pedidos_site(id,numero,criado_em,status,pagamento,total) VALUES(?,?,?,?,?,?)",
                        (o.get("id"), str(o.get("number") or ""), str(o.get("created_at") or "")[:19],
                         str(o.get("status") or ""), str(o.get("payment_status") or ""), num(o.get("total"))))
            con.execute("DELETE FROM pedidos_itens WHERE pedido_id=?", (o.get("id"),))
            for it in o.get("products") or []:
                con.execute("INSERT INTO pedidos_itens(pedido_id,variacao_id,produto_site,sku,nome,qtd) VALUES(?,?,?,?,?,?)",
                            (o.get("id"), str(it.get("variant_id") or ""), str(it.get("product_id") or ""),
                             str(it.get("sku") or ""), _texto(it.get("name")), num(it.get("quantity")) or 0))
            lidos += 1
        if len(lote) < 200:
            break
        pagina += 1
        time.sleep(1.5)
    set_meta(con, "pedidos_ate", datetime.now().strftime("%Y-%m-%d"))
    set_meta(con, "pedidos_lidos_em", agora())
    con.commit()
    recalcular(con)
    return {"pedidos": lidos}


def _rotina_pedidos():
    """A cada 10 minutos lê os pedidos novos do site (se a Nuvemshop estiver conectada)."""
    time.sleep(60)
    while True:
        try:
            if carregar_config()["token"] and not TAREFA["rodando"]:
                con = conectar()
                try:
                    sync_pedidos(con)
                finally:
                    con.close()
        except Exception:  # noqa: BLE001 - a rotina nunca derruba o painel
            pass
        time.sleep(600)


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


def _ler_csv(raw, exigir=None):
    """Aceita CSV ou planilha .xlsx (detecta pelo conteúdo)."""
    if isinstance(raw, bytes) and raw[:2] == b"PK":
        try:
            brutas = ler_xlsx(raw, exigir)
        except zipfile.BadZipFile:
            raise ValueError("Não consegui abrir a planilha .xlsx. Salve de novo como .xlsx ou .csv.")
        linhas = [[re.sub(r"^(-?\d+)\.0+$", r"\1", str(c if c is not None else "").strip()) for c in l]
                  for l in brutas]
    else:
        texto = raw.decode("utf-8-sig", errors="replace") if isinstance(raw, bytes) else raw
        primeira = texto.split("\n", 1)[0]
        delim = ";" if primeira.count(";") > primeira.count(",") else ","
        linhas = [l for l in csv.reader(io.StringIO(texto), delimiter=delim)]
    linhas = [l for l in linhas if any(c.strip() for c in l)]
    if len(linhas) < 2:
        raise ValueError("O arquivo está vazio ou sem a linha de títulos.")
    return [_cab(c) for c in linhas[0]], linhas[1:]


def _data(t):
    t = str(t or "").strip()
    if re.fullmatch(r"\d{5}(\.\d+)?", t):            # data em número do Excel
        return (datetime(1899, 12, 30) + timedelta(days=int(float(t)))).strftime("%Y-%m-%d")
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
    cab, linhas = _ler_csv(raw, [("QTDE VENDA", "QUANTIDADE VENDA", "QTD VENDA")])
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
             "cod": ("COD FORNECEDOR", "CODIGO FORNECEDOR", "COD"), "descricao": ("DESCRICAO", "PRODUTO", "ITEM NA NOTA"),
             "un": ("UN", "UNIDADE"), "quantidade": ("QUANTIDADE", "QTD", "QTDE"),
             "cfop": ("CFOP",), "ncm": ("NCM",)}


def _resumo_chegada(con, imp):
    """O que a importação trouxe: quanto já está ligado a produto, quanto falta ligar e o que vende."""
    base = "FROM notas_itens n LEFT JOIN de_para d ON d.cnpj=n.cnpj AND d.cod_fornecedor=n.cod_fornecedor WHERE n.imp_id=?"
    sem = con.execute("SELECT COUNT(*) FROM (SELECT DISTINCT n.cnpj, n.cod_fornecedor " + base +
                      " AND d.cnpj IS NULL)", (imp,)).fetchone()[0]
    ligados = con.execute(
        "SELECT p.nome, p.gz_codigo, SUM(n.quantidade*d.fator) un FROM notas_itens n JOIN de_para d "
        "ON d.cnpj=n.cnpj AND d.cod_fornecedor=n.cod_fornecedor AND d.estado='confirmado' "
        "JOIN produtos p ON p.id=d.produto_id WHERE n.imp_id=? GROUP BY p.id", (imp,)).fetchall()
    vend = {r[0]: r[1] or 0 for r in con.execute("SELECT gz_codigo, SUM(valor) FROM vendas_gz GROUP BY gz_codigo")}
    vendem = sorted([(vend.get(str(r["gz_codigo"] or ""), 0), r["nome"], r["un"]) for r in ligados], reverse=True)
    return {"sem_vinculo": sem, "produtos_ligados": len(ligados),
            "vendem": [{"nome": n, "unidades": round(u, 2)} for v, n, u in vendem if v > 0][:8]}


# ------------------------------------------------------------ nota fiscal em PDF (DANFE)

def _pdf_objetos(raw):
    return {int(n): b for n, b in re.findall(rb"(\d+) 0 obj(.*?)endobj", raw, re.S)}


def _pdf_stream(obj):
    m = re.search(rb"stream\r?\n(.*?)\r?\nendstream", obj, re.S)
    if not m:
        return b""
    dados = m.group(1)
    if b"/FlateDecode" in obj.split(b"stream", 1)[0]:
        try:
            return zlib.decompress(dados)
        except zlib.error:
            return b""
    return dados


def _pdf_cmap(obj):
    """ToUnicode: (bytes por código, {código: texto})."""
    txt = _pdf_stream(obj).decode("latin-1")
    larg = 1
    m = re.search(r"begincodespacerange\s*<([0-9A-Fa-f]+)>", txt)
    if m:
        larg = max(1, len(m.group(1)) // 2)
    mapa = {}
    def uni(h):
        try:
            return bytes.fromhex(h).decode("utf-16-be")
        except Exception:  # noqa: BLE001
            return ""
    for bloco in re.findall(r"beginbfchar(.*?)endbfchar", txt, re.S):
        for a, b in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", bloco):
            mapa[int(a, 16)] = uni(b)
    for bloco in re.findall(r"beginbfrange(.*?)endbfrange", txt, re.S):
        for a, b, c in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", bloco):
            ini, fim, base = int(a, 16), int(b, 16), uni(c)
            if base:
                for k in range(ini, min(fim, ini + 65535) + 1):
                    mapa[k] = base[:-1] + chr(ord(base[-1]) + k - ini)
    return larg, mapa


def _pdf_fontes(objs, pagina):
    fontes = {}
    m = re.search(rb"/Font\s*<<(.*?)>>", pagina, re.S)
    if not m:
        return fontes
    for nome, num in re.findall(rb"/(\w+)\s+(\d+) 0 R", m.group(1)):
        o = objs.get(int(num), b"")
        tu = re.search(rb"/ToUnicode (\d+) 0 R", o)
        larg, mapa = _pdf_cmap(objs.get(int(tu.group(1)), b"")) if tu else (1, {})
        fc = re.search(rb"/FirstChar (\d+)", o)
        ws = re.search(rb"/Widths \[(.*?)\]", o, re.S)
        larguras = [float(x) for x in ws.group(1).split()] if ws else []
        fontes[nome.decode()] = {"larg": larg, "mapa": mapa, "primeiro": int(fc.group(1)) if fc else 0, "ws": larguras}
    return fontes


def _mul(m, n):
    """Matriz m x n no formato PDF (a b c d e f)."""
    return (m[0] * n[0] + m[1] * n[2], m[0] * n[1] + m[1] * n[3], m[2] * n[0] + m[3] * n[2],
            m[2] * n[1] + m[3] * n[3], m[4] * n[0] + m[5] * n[2] + n[4], m[4] * n[1] + m[5] * n[3] + n[5])


def _pdf_linhas_pagina(objs, pagina):
    """Texto de uma página como linhas, com 2 ou mais espaços entre colunas (parecido com pdftotext -layout).
    Só biblioteca padrão; serve para PDFs com camada de texto (DANFE gerado por sistema)."""
    cont = re.search(rb"/Contents\s+(\d+) 0 R", pagina)
    if not cont:
        return []
    conteudo = _pdf_stream(objs.get(int(cont.group(1)), b"")).decode("latin-1")
    fontes = _pdf_fontes(objs, pagina)
    ctm, pilha = (1, 0, 0, 1, 0, 0), []
    tlm = tm = (1, 0, 0, 1, 0, 0)
    fonte, tam = None, 1.0
    itens = []
    tokens = re.findall(r"\[(?:[^\]\\]|\\.)*\]|<[0-9A-Fa-f\s]*>|\((?:[^()\\]|\\.)*\)|/[^\s/\[\]<>()]+|[-+]?\d*\.?\d+|[A-Za-z\'\"*]+", conteudo)
    pilha_op = []
    def mostrar(dados):
        if not fonte:
            return
        f = fontes.get(fonte, {"larg": 1, "mapa": {}, "ws": [], "primeiro": 0})
        txt, larg_total = "", 0.0
        for i in range(0, len(dados) - f["larg"] + 1, f["larg"]):
            cod = int.from_bytes(dados[i:i + f["larg"]], "big")
            txt += f["mapa"].get(cod, chr(cod) if 32 <= cod < 256 and not f["mapa"] else "")
            k = cod - f["primeiro"]
            larg_total += (f["ws"][k] if 0 <= k < len(f["ws"]) else 500) / 1000.0
        g = _mul(tm, ctm)
        escala = math.hypot(g[0], g[1]) * tam
        itens.append((g[4], g[5], txt, larg_total * escala, escala))
    for t in tokens:
        if re.fullmatch(r"[-+]?\d*\.?\d+", t) or t.startswith(("/", "[", "<", "(")):
            pilha_op.append(t)
            continue
        op, args = t, pilha_op
        pilha_op = []
        try:
            if op == "q":
                pilha.append(ctm)
            elif op == "Q" and pilha:
                ctm = pilha.pop()
            elif op == "cm" and len(args) >= 6:
                ctm = _mul(tuple(float(x) for x in args[-6:]), ctm)
            elif op == "BT":
                tlm = tm = (1, 0, 0, 1, 0, 0)
            elif op == "Tf" and len(args) >= 2:
                fonte, tam = args[-2][1:], float(args[-1])
            elif op == "Tm" and len(args) >= 6:
                tlm = tm = tuple(float(x) for x in args[-6:])
            elif op in ("Td", "TD") and len(args) >= 2:
                tlm = _mul((1, 0, 0, 1, float(args[-2]), float(args[-1])), tlm)
                tm = tlm
            elif op in ("Tj", "'", '"') and args:
                a = args[-1]
                if a.startswith("<"):
                    mostrar(bytes.fromhex(re.sub(r"\s", "", a[1:-1]) + ("0" if len(re.sub(r"\s", "", a[1:-1])) % 2 else "")))
                elif a.startswith("("):
                    mostrar(re.sub(r"\\(.)", r"\1", a[1:-1]).encode("latin-1", "replace"))
            elif op == "TJ" and args and args[-1].startswith("["):
                for p in re.findall(r"<[0-9A-Fa-f\s]*>|\((?:[^()\\]|\\.)*\)", args[-1]):
                    if p.startswith("<"):
                        mostrar(bytes.fromhex(re.sub(r"\s", "", p[1:-1])))
                    else:
                        mostrar(re.sub(r"\\(.)", r"\1", p[1:-1]).encode("latin-1", "replace"))
        except (ValueError, IndexError):
            continue
    # agrupa por linha (mesma altura) e ordena da esquerda para a direita
    itens = [i for i in itens if i[2].strip() or i[2]]
    itens.sort(key=lambda i: (-round(i[1] / 2.0), i[0]))
    linhas, atual, y_ant = [], [], None
    for it in itens:
        y = round(it[1] / 2.0)
        if y_ant is not None and y != y_ant:
            linhas.append(atual)
            atual = []
        atual.append(it)
        y_ant = y
    if atual:
        linhas.append(atual)
    saida = []
    for l in linhas:
        l.sort(key=lambda i: i[0])
        txt, fim = "", None
        for x, y, t, w, esc in l:
            if fim is not None:
                folga = x - fim
                if folga > esc * 0.9:
                    txt += "  "
                elif folga > esc * 0.18 and not txt.endswith(" ") and not t.startswith(" "):
                    txt += " "
            txt += t
            fim = x + w
        saida.append(txt.rstrip())
    return saida


def pdf_linhas(raw):
    objs = _pdf_objetos(raw)
    paginas = sorted((n for n, b in objs.items() if re.search(rb"/Type\s*/Page\b", b) and b"/Contents" in b))
    linhas = []
    for n in paginas:
        linhas += _pdf_linhas_pagina(objs, objs[n])
    return linhas


def nota_pdf_para_linhas(raw):
    """Lê um DANFE em PDF e devolve (cabeçalho da nota, itens, aviso)."""
    linhas = pdf_linhas(raw)
    if not linhas:
        raise ValueError("Não consegui ler texto desse PDF. Se for foto ou digitalização, exporte os itens em CSV ou Excel.")
    texto = "\n".join(linhas)
    if "DANFE" not in texto.upper() and "DADOS DOS PRODUTOS" not in texto.upper():
        raise ValueError("Esse PDF não parece uma nota fiscal (DANFE).")
    m = re.search(r"EMISS[ÃA]O:\s*(\d{2}/\d{2}/\d{4})", texto) or re.search(r"DATA DA EMISS[ÃA]O\s+(\d{2}/\d{2}/\d{4})", texto)
    emissao = _data(m.group(1)) if m else None
    nome = re.search(r"RECEBEMOS DE\s+'?\"?([^'\"]+?)['\"]?\s+OS PRODUTOS", texto)
    ini_dest = texto.upper().find("DESTINATÁRIO/REMETENTE")
    antes = texto[:ini_dest] if ini_dest > 0 else texto
    cn = re.findall(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}", antes)
    nota = re.search(r"N[º°o]\s*0*(\d+)", texto)
    serie = re.search(r"S[ÉE]RIE[:\s]+(\d+)", texto)
    itens, dentro = [], False
    for l in linhas:
        if "DADOS DOS PRODUTOS" in l.upper():
            dentro = True
            continue
        if not dentro:
            continue
        if re.match(r"\s*(DADOS ADICIONAIS|C[ÁA]LCULO DO ISSQN)", l.upper()):
            break
        partes = re.split(r"\s{2,}", l.strip())
        ix = next((i for i, p in enumerate(partes) if re.fullmatch(r"\d{8}", p)), None)
        if ix is None or ix < 2 or len(partes) < ix + 8:
            continue
        try:
            qtd = num_br(partes[ix + 4])
        except ValueError:
            continue
        itens.append({"cod": partes[0], "descricao": " ".join(partes[1:ix]), "ncm": partes[ix], "cfop": partes[ix + 2],
                      "un": partes[ix + 3], "quantidade": qtd, "total": num_br(partes[ix + 6 + 1]) if len(partes) > ix + 7 else 0.0})
    if not (emissao and cn and nota):
        raise ValueError("Não achei a data, o CNPJ do fornecedor ou o número da nota nesse PDF.")
    if not itens:
        raise ValueError("Não achei a tabela de produtos nesse PDF.")
    cab = {"emissao": emissao, "fornecedor": (nome.group(1) if nome else "")[:80], "cnpj": re.sub(r"\D", "", cn[0]),
           "nota": nota.group(1), "serie": serie.group(1) if serie else ""}
    soma = round(sum(i["total"] for i in itens), 2)
    return cab, itens, soma


def importar_notas_pdf(con, raw, arquivo="", quem="painel"):
    cab, itens, soma = nota_pdf_para_linhas(raw)
    saida = io.StringIO()
    w = csv.writer(saida, delimiter=";")
    w.writerow(["emissao", "fornecedor", "cnpj", "nota", "serie", "cod_fornecedor", "descricao", "un", "quantidade", "cfop", "ncm"])
    for i in itens:
        w.writerow([cab["emissao"], cab["fornecedor"], cab["cnpj"], cab["nota"], cab["serie"], i["cod"], i["descricao"],
                    i["un"], str(i["quantidade"]).replace(".", ","), i["cfop"], i["ncm"]])
    r = importar_notas(con, saida.getvalue().encode("utf-8"), arquivo, quem)
    r["nota_pdf"] = {"nota": cab["nota"], "fornecedor": cab["fornecedor"], "itens": len(itens), "soma_itens": soma}
    return r


def importar_notas(con, raw, arquivo="", quem="painel"):
    """Itens das notas de entrada. Reimportar o mesmo arquivo não duplica nada."""
    cab, linhas = _ler_csv(raw, [("EMISSAO", "DATA"), ("QUANTIDADE", "QTD", "QTDE")])
    ix = {k: _col(cab, *v, exato=True) for k, v in NOTA_COLS.items()}
    if ix["cnpj"] is None:                  # sem CNPJ no arquivo: o fornecedor identifica a nota
        ix["cnpj"] = ix["fornecedor"]
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
    return {"ok": True, "linhas": total, "novas": novas, "repetidas": total - novas,
            "chegada": _resumo_chegada(con, imp) if novas else None}


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
    for r in con.execute("SELECT gz_codigo, inicio, MAX(fim) AS fim, unidade, SUM(qtd) AS q FROM vendas_gz "
                         "GROUP BY gz_codigo, inicio, unidade"):
        ven.setdefault(r["gz_codigo"], []).append((r["inicio"], r["unidade"], r["q"] or 0, r["fim"]))
    sitev = {}
    for r in con.execute(
            "SELECT k.produto_id pid, substr(o.criado_em,1,10) d, SUM(i.qtd) q FROM pedidos_itens i "
            "JOIN pedidos_site o ON o.id=i.pedido_id JOIN canais k ON k.canal='nuvemshop' "
            "AND k.id_variacao=i.variacao_id WHERE o.status<>'cancelled' GROUP BY k.produto_id, d"):
        sitev.setdefault(r["pid"], []).append((r["d"], r["q"] or 0))
    for p in prods:
        est, aviso = None, None
        if p["estoque"] is not None and p["conferido_em"]:
            dia = p["conferido_em"][:10]
            e = sum(q for em, q in ent.get(p["id"], []) if em > dia)
            v = 0.0
            cruza = False
            for ini, un, q, fim in ven.get(str(p["gz_codigo"] or ""), []):
                if ini > dia:
                    if un == "KG":
                        aviso = "peso"
                    v += q
                elif q and (fim or "") >= dia:
                    cruza = True          # período de vendas que contém o dia da contagem: não dá para saber quanto saiu depois
            v += sum(q for d, q in sitev.get(p["id"], []) if d > dia)
            if aviso != "peso":
                est = round(p["estoque"] + e - v, 2)
                if est < 0:
                    aviso = "negativo"          # vendas descontadas maiores que a contagem: confira o período importado
                elif cruza:
                    aviso = "periodo"           # estimativa pode estar acima do real: vendas do dia da contagem não entraram
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
    site = [dict(r) for r in con.execute(
        "SELECT substr(o.criado_em,1,10) AS dia, o.numero, i.qtd FROM pedidos_itens i JOIN pedidos_site o "
        "ON o.id=i.pedido_id JOIN canais k ON k.canal='nuvemshop' AND k.id_variacao=i.variacao_id "
        "WHERE k.produto_id=? AND o.status<>'cancelled' AND substr(o.criado_em,1,10)>? ORDER BY o.criado_em DESC",
        (pid, dia))]
    return {"contagem_em": dia, "entradas": entradas, "vendas": vendas, "site": site}


def prioridades(con, cor="", q="", pagina=1, por_pagina=50, tudo=False):
    """Semáforo de risco por produto, só com dados (sem contar prateleira):
    curva ABC das vendas, estoque estimado/oficial, cobertura em dias e última compra.
    vermelho = provável falta | amarelo = conferir/comprar | verde = ok | cinza = parado."""
    per = con.execute("SELECT MIN(inicio) i, MAX(fim) f FROM vendas_gz").fetchone()
    if not per or not per["i"]:
        return {"periodo": None, "dias": 0, "resumo": {}, "itens": [], "total": 0,
                "aviso": "Importe o relatório de vendas (com as datas) para ver as prioridades."}
    dias = max(1, (datetime.strptime(per["f"], "%Y-%m-%d") - datetime.strptime(per["i"], "%Y-%m-%d")).days + 1)
    ven = {}
    for r in con.execute("SELECT gz_codigo, unidade, SUM(qtd) q, SUM(valor) v FROM vendas_gz GROUP BY gz_codigo, unidade"):
        d = ven.setdefault(r["gz_codigo"], {"q": 0.0, "v": 0.0, "kg": False})
        d["q"] += r["q"] or 0
        d["v"] += r["v"] or 0
        d["kg"] = d["kg"] or r["unidade"] == "KG"
    total_v = sum(d["v"] for d in ven.values()) or 1.0
    acum, abc = 0.0, {}
    for cod, d in sorted(ven.items(), key=lambda kv: -kv[1]["v"]):
        abc[cod] = "A" if acum < 0.8 * total_v else ("B" if acum < 0.95 * total_v else "C")
        acum += d["v"]
    hoje = datetime.now().strftime("%Y-%m-%d")
    corte = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
    entr = {}
    for r in con.execute(
            "SELECT d.produto_id pid, MAX(n.emissao) ult, "
            "SUM(CASE WHEN n.emissao>=? THEN n.quantidade*d.fator ELSE 0 END) u60 "
            "FROM notas_itens n JOIN de_para d ON d.cnpj=n.cnpj AND d.cod_fornecedor=n.cod_fornecedor "
            "WHERE d.estado='confirmado' AND d.produto_id IS NOT NULL GROUP BY d.produto_id", (corte,)):
        entr[r["pid"]] = (r["ult"], r["u60"] or 0)
    no_site = {r[0] for r in con.execute("SELECT DISTINCT produto_id FROM canais WHERE canal='nuvemshop'")}
    sem_vinculo = con.execute(
        "SELECT COUNT(*) FROM (SELECT DISTINCT n.cnpj, n.cod_fornecedor FROM notas_itens n "
        "LEFT JOIN de_para d ON d.cnpj=n.cnpj AND d.cod_fornecedor=n.cod_fornecedor "
        "WHERE d.cnpj IS NULL)").fetchone()[0]
    itens = []
    for p in con.execute("SELECT id, sku, gz_codigo, ean, nome, grupo, estoque, estimado, est_aviso, conferido_em, "
                         "ultima_compra, situacao FROM produtos WHERE ativo IS NOT 0"):
        if (p["nome"] or "").strip().upper() in ("DIVERSOS", "ENTREGA"):
            continue          # lançamentos genéricos do caixa, não são produtos
        cod = str(p["gz_codigo"] or "")
        d = ven.get(cod)
        base = p["estimado"] if p["estimado"] is not None else p["estoque"]
        contado = p["estoque"] is not None
        taxa = (d["q"] / dias) if d and not d["kg"] else None
        cob = (base / taxa) if (base is not None and taxa) else None
        classe = abc.get(cod, "-")
        dias_compra = None
        ult_ent, un60 = entr.get(p["id"], (None, 0))
        for ref in (p["ultima_compra"], ult_ent):          # vale a compra mais recente (Gestor Z ou nota)
            if ref:
                try:
                    dd = (datetime.strptime(hoje, "%Y-%m-%d") - datetime.strptime(str(ref)[:10], "%Y-%m-%d")).days
                    dias_compra = dd if dias_compra is None else min(dias_compra, dd)
                except ValueError:
                    pass
        if not d:
            sem, why = "cinza", "Sem venda no período"
        elif base is not None and base <= 0 and p["est_aviso"] == "negativo":
            sem, why = "amarelo", "Estimativa negativa: confira o período das vendas importadas e a contagem"
        elif base is not None and base <= 0:
            sem, why = "vermelho", "Estoque estimado zerado"
        elif cob is not None and cob < 7:
            sem, why = "vermelho", f"Cobertura de {cob:.0f} dia(s)"
        elif not contado and classe in ("A", "B") and (dias_compra is None or dias_compra > 60):
            sem, why = "vermelho", "Vende bem, sem contagem e sem compra recente"
        elif cob is not None and cob < 21:
            sem, why = "amarelo", f"Cobertura de {cob:.0f} dias"
        elif not contado and classe in ("A", "B"):
            sem, why = "amarelo", "Vende bem e nunca foi contado"
        else:
            sem, why = "verde", "Sem alerta"
        if un60 and not contado and sem in ("vermelho", "amarelo") and d:
            sem, why = "amarelo", f"Entrou nota em {ult_ent[8:10]}/{ult_ent[5:7]} ({un60:g} un): confira a prateleira antes de comprar"
        sugerir = None
        if taxa and sem in ("vermelho", "amarelo"):
            sugerir = max(0, round(taxa * 30 - (base if base is not None else 0) - (0 if contado else un60)))
        itens.append({"id": p["id"], "sku": p["sku"], "nome": p["nome"], "grupo": p["grupo"], "classe": classe,
                      "cor": sem, "motivo": why, "estoque": p["estoque"], "estimado": p["estimado"],
                      "peso": bool(d and d["kg"]), "vendas_qtd": round(d["q"], 2) if d else 0,
                      "vendas_valor": round(d["v"], 2) if d else 0, "por_dia": round(taxa, 2) if taxa else None,
                      "cobertura_dias": round(cob, 1) if cob is not None else None,
                      "ultima_compra": p["ultima_compra"], "ultima_entrada": ult_ent, "entrada_60d": round(un60, 2),
                      "nuvemshop": p["id"] in no_site, "contar": (not contado) and classe in ("A", "B"),
                      "comprar_30d": sugerir})
    ordem = {"vermelho": 0, "amarelo": 1, "verde": 2, "cinza": 3}
    resumo = collections.Counter(i["cor"] for i in itens)
    if cor:
        itens = [i for i in itens if i["cor"] == cor]
    if q:
        qq = _sem_acento(q).upper()
        itens = [i for i in itens if qq in _sem_acento(i["nome"] or "").upper() or qq in str(i["sku"] or "")]
    itens.sort(key=lambda i: (ordem[i["cor"]], -i["vendas_valor"]))
    fila = sum(1 for i in itens if i["contar"])
    total = len(itens)
    if not tudo:
        itens = itens[(pagina - 1) * por_pagina: pagina * por_pagina]
    return {"periodo": [per["i"], per["f"]], "dias": dias, "resumo": dict(resumo), "total": total,
            "para_contar": fila, "itens": itens, "notas_sem_vinculo": sem_vinculo}


_PACOTE = re.compile(r"\b(?:CX|FD|PCT|DP|CT|PC)[- ]?(\d{1,4})\s*UN")
_DICA_PACOTE = re.compile(r"\bC/\s*\d|\b\d{1,4}\s*X\s*\d|\b\d{1,4}\s*(?:PCS?|P|UN|UND|UNID)\b|\bDZ\b")
_TAM = re.compile(r"(\d+(?:[.,]\d+)?)\s*(ML|LTS?|L|KG|GR?|M)\b")


def _tamanhos(t):
    saida = set()
    for n, u in _TAM.findall(_sem_acento(t).upper()):
        v = float(n.replace(",", "."))
        u = {"LT": "L", "LTS": "L", "GR": "G"}.get(u, u)
        if u == "L":
            v, u = v * 1000, "ML"
        if u == "KG":
            v, u = v * 1000, "G"
        saida.add((round(v), u))
    return saida


def _fator_nota(descricao, un):
    """Unidades por caixa/pacote só quando o texto da nota é claro (CX-12UN). Senão None."""
    d = _sem_acento(descricao).upper()
    m = _PACOTE.search(d)
    if m and 1 <= int(m.group(1)) <= 2000:
        return float(m.group(1))
    if str(un or "").strip().upper() in ("UN", "UNI", "1UN", "UND") and not _DICA_PACOTE.search(d):
        return 1.0
    return None


def _cobre(descricao, nome):
    """Todas as palavras do nome do produto aparecem na descrição da nota (evita marca/variação errada)."""
    ref = _sem_acento(descricao).upper()
    seco = re.sub(r"[^A-Z0-9]", "", ref)
    nt = _tokens(descricao)
    for w in _tokens(nome):
        if w in nt or any(len(x) >= 4 and len(w) >= 4 and (x.startswith(w) or w.startswith(x)) for x in nt):
            continue
        if re.search(r"\d", w) and w in seco:
            continue
        return False
    return True


def auto_vincular_notas(con, aplicar=False, quem="auto"):
    """Liga sozinho os itens das notas aos produtos quando é seguro: nome muito parecido, mesmo
    tamanho (ml/L/kg/g) e fator de caixa claro no texto. O resto fica para você decidir.
    Tudo que entra aqui fica marcado 'auto' e pode ser desfeito de uma vez."""
    itens = con.execute(
        "SELECT n.cnpj, n.cod_fornecedor, MAX(n.descricao) descricao, MAX(n.un) un FROM notas_itens n "
        "LEFT JOIN de_para d ON d.cnpj=n.cnpj AND d.cod_fornecedor=n.cod_fornecedor "
        "WHERE d.cnpj IS NULL GROUP BY n.cnpj, n.cod_fornecedor").fetchall()
    ok, duvida, afetados = [], 0, set()
    for it in itens:
        sug = sugerir_produtos(con, it["descricao"], 2)
        fator = _fator_nota(it["descricao"], it["un"])
        if not sug or fator is None:
            duvida += 1
            continue
        top = sug[0]
        gap = top["parecido"] - (sug[1]["parecido"] if len(sug) > 1 else 0)
        tn, tp = _tamanhos(it["descricao"]), _tamanhos(top["nome"])
        if (top["parecido"] < 0.70 or gap < 0.05 or (tn and tp and tn != tp) or (bool(tn) != bool(tp))
                or not _cobre(it["descricao"], top["nome"])):
            duvida += 1
            continue
        ok.append((it, top, fator))
    if aplicar:
        for it, top, fator in ok:
            con.execute("INSERT OR IGNORE INTO de_para VALUES(?,?,?,?,'confirmado',?,?)",
                        (it["cnpj"], it["cod_fornecedor"], top["id"], fator, quem, agora()))
            afetados.add(top["id"])
        con.commit()
        recalcular(con, afetados)
    amostra = [{"nota": it["descricao"][:60], "produto": top["nome"][:60], "fator": fator,
                "parecido": top["parecido"]} for it, top, fator in ok[:12]]
    return {"aplicado": bool(aplicar), "seguros": len(ok), "para_decidir": duvida, "amostra": amostra}


def desfazer_auto_vinculos(con):
    ids = {r[0] for r in con.execute("SELECT DISTINCT produto_id FROM de_para WHERE quem='auto'")}
    n = con.execute("DELETE FROM de_para WHERE quem='auto'").rowcount
    con.commit()
    recalcular(con, ids)
    return {"ok": True, "removidos": n}


_RUIDO_NOTA = [re.compile(p, re.I) for p in (r"\s*\b(?:CX|FD|DP|PCT|CT|PC)[- ]?\d+\s*UN\b.*$", r"\bQTD\.?.*$",
                                              r"^\s*\d+(?:\.\d+)*\s+", r"\((?:E|NEW|NOVO|NOVA)\)")]


def _nome_da_nota(desc):
    t = str(desc or "")
    for rx in _RUIDO_NOTA:
        t = rx.sub(" ", t)
    t = " ".join(t.split()).strip(" -/.")
    t = t.title()
    t = re.sub(r"(\d)(Ml|Kg|G|L|Lt|Un|M|Cm|Mm)\b", lambda m: m.group(1) + m.group(2).lower() if m.group(2) in ("Ml", "Kg", "Cm", "Mm", "Lt", "Un") else m.group(1) + m.group(2), t)
    return t[:150]


def itens_so_na_nota(con):
    """Itens de nota fiscal que não têm produto no painel (nem parecido). Chegou na nota = existe."""
    base = con.execute(
        "SELECT n.cnpj, n.cod_fornecedor, MAX(n.descricao) descricao, MAX(n.un) un, MAX(n.emissao) ultima, "
        "MAX(n.fornecedor) fornecedor, SUM(n.quantidade) qtd FROM notas_itens n "
        "LEFT JOIN de_para d ON d.cnpj=n.cnpj AND d.cod_fornecedor=n.cod_fornecedor "
        "WHERE d.cnpj IS NULL GROUP BY n.cnpj, n.cod_fornecedor").fetchall()
    saida = []
    da_nota = {r[0] for r in con.execute("SELECT id FROM produtos WHERE obs LIKE 'Veio da nota fiscal%'")}
    for it in base:
        sug = [x for x in sugerir_produtos(con, it["descricao"], 6) if x["id"] not in da_nota][:1]
        if sug and sug[0]["parecido"] >= 0.6 and _cobre(it["descricao"], sug[0]["nome"]):
            continue                      # já parece existir no painel: vincule na aba Itens das notas
        saida.append({"cnpj": it["cnpj"], "cod": it["cod_fornecedor"], "nome": _nome_da_nota(it["descricao"]),
                      "nota": it["descricao"], "fornecedor": it["fornecedor"], "ultima": it["ultima"],
                      "fator": _fator_nota(it["descricao"], it["un"])})
    saida.sort(key=lambda x: x["ultima"] or "", reverse=True)
    return saida


def trazer_itens_da_nota(con, quem="painel"):
    """Cria no painel (sem mandar nada para o site) os produtos que só existem na nota, para entrarem
    na lista de cadastro. Sem preço e sem código de barras: isso fica marcado como falta."""
    n = 0
    for it in itens_so_na_nota(con):
        if len(it["nome"]) < 3:
            continue
        cur = con.execute(
            "INSERT INTO produtos(nome,unidade,ativo,origem,obs,atualizado_em) VALUES(?,'UN',1,'manual',?,?)",
            (it["nome"], f"Veio da nota fiscal ({it['fornecedor'][:40]}): {it['nota'][:80]}", agora()))
        pid = cur.lastrowid
        con.execute("INSERT OR IGNORE INTO de_para VALUES(?,?,?,?,'confirmado',?,?)",
                    (it["cnpj"], it["cod"], pid, it["fator"] or 1.0, f"{quem}-nota", agora()))
        con.execute("INSERT INTO historico(produto_id,quando,campo,de,para,origem) VALUES(?,?,?,?,?,?)",
                    (pid, agora(), "cadastro", "", "trazido da nota fiscal", quem))
        n += 1
    con.commit()
    return {"ok": True, "criados": n}


def central(con):
    """Visão única: o que falta fazer, juntando vendas, notas, estoque e site."""
    pr = prioridades(con, tudo=True)
    itens = pr.get("itens", [])
    sem_site = [i for i in itens if not i["nuvemshop"] and (i["vendas_valor"] > 0 or i["entrada_60d"] > 0)]
    sem_site.sort(key=lambda i: -(i["vendas_valor"] or 0))
    auto = auto_vincular_notas(con, False)
    return {
        "periodo": pr.get("periodo"), "aviso": pr.get("aviso"),
        "cores": pr.get("resumo", {}), "para_contar": pr.get("para_contar", 0),
        "comprar": sum(1 for i in itens if i["cor"] in ("vermelho", "amarelo") and i["comprar_30d"]),
        "notas_sem_vinculo": pr.get("notas_sem_vinculo", 0), "auto_seguros": auto["seguros"],
        "falta_no_site": len(sem_site),
        "falta_no_site_top": [{"nome": i["nome"], "vendas_valor": i["vendas_valor"], "entrou": i["entrada_60d"]}
                              for i in sem_site[:10]],
        "ultima_nuvemshop": con.execute("SELECT valor FROM meta WHERE chave='nuvemshop_sync_em'").fetchone()[0]
        if con.execute("SELECT valor FROM meta WHERE chave='nuvemshop_sync_em'").fetchone() else None}


def prioridades_csv(con, cor=""):
    r = prioridades(con, cor=cor, tudo=True)
    saida = io.StringIO()
    w = csv.writer(saida, delimiter=";")
    w.writerow(["cor", "classe", "sku", "produto", "grupo", "motivo", "estoque_oficial", "estoque_estimado",
                "vendas_qtd", "vendas_valor", "por_dia", "cobertura_dias", "ultima_compra", "ultima_entrada_nota", "entrou_60d", "na_nuvemshop", "contar", "comprar_30d"])
    f = lambda x: "" if x is None else str(x).replace(".", ",")
    for i in r["itens"]:
        w.writerow([i["cor"], i["classe"], i["sku"] or "", i["nome"], i["grupo"] or "", i["motivo"],
                    f(i["estoque"]), f(i["estimado"]), f(i["vendas_qtd"]), f(i["vendas_valor"]), f(i["por_dia"]),
                    f(i["cobertura_dias"]), i["ultima_compra"] or "", i["ultima_entrada"] or "", f(i["entrada_60d"]),
                    "sim" if i["nuvemshop"] else "não", "sim" if i["contar"] else "",
                    "" if i["comprar_30d"] is None else i["comprar_30d"]])
    return "﻿" + saida.getvalue()


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


# leitor de números (OCR) que roda no próprio navegador: arquivos locais, nada sai da sua rede
OCR_ARQUIVOS = {"tesseract.min.js", "worker.min.js", "tesseract-core-simd-lstm.wasm.js", "tesseract-core-lstm.wasm.js", "eng.traineddata.gz"}


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


def conferidos_recentes(con, limite=300):
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


def _sync_pedidos_tarefa(progresso):
    con = conectar()
    try:
        return sync_pedidos(con, progresso)
    finally:
        con.close()


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
        if u.path.startswith("/vendor/ocr/") and u.path[12:] in OCR_ARQUIVOS:
            return self._arquivo("vendor/ocr/" + u.path[12:], "application/octet-stream" if u.path.endswith(".gz") else "text/javascript; charset=utf-8")
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
            if u.path == "/prioridades":
                return self._arquivo("prioridades.html", "text/html; charset=utf-8")
            if u.path == "/api/cadastrar/so-nota":
                lista = itens_so_na_nota(con)
                return self._json({"total": len(lista), "amostra": lista[:15]})
            if u.path == "/api/ifood-api/previa":
                ids = [x for x in qs.get("ids", "").split(",") if x.strip().isdigit()]
                try:
                    promo = float(qs["promo"].replace(",", ".")) if qs.get("promo") else None
                    return self._json(previa_ifood_item(con, ids, modo=qs.get("modo", "post"), promo=promo))
                except ValueError as e:
                    return self._json({"erro": str(e)}, 400)
            if u.path == "/api/ifood-api/candidatos":
                try:
                    return self._json(candidatos_ifood(con, qs.get("lista", ""), mostrar_enviados=qs.get("todos") == "1"))
                except ValueError as e:
                    return self._json({"erro": str(e)}, 400)
            if u.path == "/api/cruzamento/sugestoes":
                return self._json(sugestoes_em_massa(con))
            if u.path == "/api/cruzamento":
                return self._json(cruzamento_precos(con, qs.get("tipo", "")))
            if u.path == "/api/ifood-api/comparar":
                return self._json(comparar_canais(con))
            if u.path == "/api/ifood-api/estado":
                return self._json(estado_ifood())
            if u.path == "/api/central":
                return self._json(central(con))
            if u.path == "/api/entradas/auto-previa":
                return self._json(auto_vincular_notas(con, False))
            if u.path == "/api/prioridades":
                return self._json(prioridades(con, qs.get("cor", ""), qs.get("q", ""),
                                              max(1, int(qs.get("pagina", 1))), 50))
            if u.path == "/api/prioridades.csv":
                corpo = prioridades_csv(con, qs.get("cor", "")).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header("Content-Disposition",
                                 f'attachment; filename="prioridades_{datetime.now():%Y-%m-%d}.csv"')
                self.send_header("Content-Length", str(len(corpo)))
                self.end_headers()
                self.wfile.write(corpo)
                return
            if u.path == "/api/importacoes":
                return self._json(listar_importacoes(con))
            if u.path == "/api/nuvemshop/previa":
                return self._json(previa_nuvemshop(con, qs.get("modo", "zerar")))
            if u.path in ("/api/ifood/previa", "/api/ifood/planilha", "/api/ifood/revisar.csv"):
                try:
                    divisor = float(str(qs.get("divisor", "0.70")).replace(",", "."))
                except ValueError:
                    raise ValueError("Divisor inválido.")
                plano = planilha_ifood(con, divisor, "ifood" if qs.get("so_novos") == "1" else None)
                if u.path == "/api/ifood/previa":
                    r = dict(plano)
                    r["itens"] = plano["itens"][:12]
                    r["catalogo_em"] = (con.execute("SELECT valor FROM meta WHERE chave='ifood_catalogo_em'").fetchone() or [None])[0]
                    return self._json(r)
                if u.path == "/api/ifood/revisar.csv":
                    corpo = revisar_ifood_csv(plano).encode("utf-8")
                    tipo, nome = "text/csv; charset=utf-8", "ifood_revisar.csv"
                else:
                    parte = max(1, int(qs.get("parte", 1)))
                    corpo = xlsx_ifood(plano["itens"][(parte - 1) * LINHAS_POR_PLANILHA: parte * LINHAS_POR_PLANILHA],
                                       qs.get("canal", "shop") if qs.get("canal", "shop") in ("app", "shop", "white") else "shop")
                    tipo, nome = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", f"ifood_planilha_{parte}.xlsx"
                self.send_response(200)
                self.send_header("Content-Type", tipo)
                self.send_header("Content-Length", str(len(corpo)))
                self.send_header("Content-Disposition", f'attachment; filename="{nome}"')
                self.end_headers()
                self.wfile.write(corpo)
                return
            if u.path in ("/api/n99/previa", "/api/n99/editar.xlsx", "/api/n99/adicionar.xlsx", "/api/n99/revisar.csv"):
                try:
                    divisor = float(str(qs.get("divisor", "0.70")).replace(",", "."))
                except ValueError:
                    raise ValueError("Divisor inválido.")
                plano = plano_99(con, divisor)
                teste = qs.get("teste") == "1"
                if u.path == "/api/n99/previa":
                    r = {k: v for k, v in plano.items() if k not in ("editar", "lista_conferir", "lista_novos")}
                    r["amostra"] = [{"nome": x["nome"], "qtd": x["qtd"], "preco_txt": x["preco_txt"], "motivo": x["motivo"]} for x in plano["editar"][:12]]
                    r["catalogo_em"] = (con.execute("SELECT valor FROM meta WHERE chave='n99_catalogo_em'").fetchone() or [None])[0]
                    return self._json(r)
                if u.path == "/api/n99/revisar.csv":
                    corpo, tipo, nome = revisar_99_csv(plano).encode("utf-8"), "text/csv; charset=utf-8", "99food_revisar.csv"
                else:
                    corpo = xlsx_99_editar(plano, teste) if u.path.endswith("editar.xlsx") else xlsx_99_adicionar(plano, teste)
                    tipo = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    nome = ("99food_editar" if u.path.endswith("editar.xlsx") else "99food_adicionar") + ("_teste" if teste else "") + ".xlsx"
                self.send_response(200)
                self.send_header("Content-Type", tipo)
                self.send_header("Content-Length", str(len(corpo)))
                self.send_header("Content-Disposition", f'attachment; filename="{nome}"')
                self.end_headers()
                self.wfile.write(corpo)
                return
            if u.path == "/api/categorias/sugestoes":
                return self._json(listar_sugestoes(con, qs.get("confianca", "alta"), qs.get("q", ""),
                                                   max(1, int(qs.get("pagina", 1)))))
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
                id_foto = u.path.rsplit("/", 1)[1]
                if not id_foto.isdigit():
                    return self._json({"erro": "Sem foto."}, 404)
                arq = os.path.join(fotos_dir(), f"{int(id_foto)}.jpg")
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
            if u.path.startswith("/api/produto/") and u.path.endswith("/sugestoes-preco"):
                return self._json({"itens": sugestoes_preco(con, int(u.path.split("/")[3]))})
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
        except (ValueError, KeyError) as e:     # falta de passo do usuário, não defeito do painel
            self._json({"erro": str(e)}, 400)
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
            if u.path == "/api/cruzamento/definir":
                d = json.loads(self._corpo() or b"{}")
                return self._json(definir_preco_oficial(con, d.get("pares") or [], unquote(self.headers.get("X-Quem") or "painel")[:40]))
            if u.path == "/api/cruzamento/aplicar-sugestoes":
                d = json.loads(self._corpo() or b"{}")
                return self._json(aplicar_sugestoes(con, d.get("pares") or [], unquote(self.headers.get("X-Quem") or "painel")[:40]))
            if u.path == "/api/cruzamento/aplicar":
                d = json.loads(self._corpo() or b"{}")
                return self._json(aplicar_preco_oficial(con, d.get("ids") or [], unquote(self.headers.get("X-Quem") or "painel")[:40]))
            if u.path == "/api/envio/marcar":
                return self._json(marcar_envio(con, str(json.loads(self._corpo() or b"{}").get("destino") or "")))
            if u.path.startswith("/api/produto/") and u.path.endswith("/desfazer-contagem"):
                dados = json.loads(self._corpo() or b"{}")
                return self._json(desfazer_contagem(con, int(u.path.split("/")[3]),
                                                    (str(dados.get("quem") or "").strip() or "painel")[:40]))
            if u.path.startswith("/api/produto/"):
                dados = json.loads(self._corpo() or b"{}")
                return self._json(alterar(con, int(u.path.rsplit("/", 1)[1]), dados))
            if u.path.startswith("/api/nuvemshop/produto/"):
                d = json.loads(self._corpo() or b"{}")
                return self._json(enviar_produto_site(con, int(u.path.rsplit("/", 1)[1]), (d.get("quem") or "painel")[:40],
                                                      d.get("campos") or []))
            if u.path == "/api/ifood-api/conectar":
                try:
                    return self._json(conectar_ifood(json.loads(self._corpo() or b"{}")))
                except ValueError as e:
                    return self._json({"erro": str(e)}, 400)
            if u.path == "/api/ifood-api/enviar":
                try:
                    d = json.loads(self._corpo() or b"{}")
                    promo = float(str(d["promo"]).replace(",", ".")) if d.get("promo") not in (None, "") else None
                    return self._json(enviar_ifood_item(con, d.get("ids") or [], d.get("ambiente") or "teste",
                                                        unquote(self.headers.get("X-Quem") or "painel")[:40],
                                                        modo=d.get("modo") or "post", promo=promo,
                                                        confirmo_reset=(str(d.get("confirmo") or "").strip().upper() == "RESET"),
                                                        confirmo_real=(str(d.get("confirmo") or "").strip().upper() == "ENVIAR")))
                except ValueError as e:
                    return self._json({"erro": str(e)}, 400)
            if u.path == "/api/ifood-api/testar":
                try:
                    return self._json(testar_ifood(json.loads(self._corpo() or b"{}").get("ambiente")))
                except ValueError as e:
                    return self._json({"erro": str(e)}, 400)
            if u.path == "/api/config/nuvemshop":
                d = json.loads(self._corpo() or b"{}")
                return self._json(conectar_nuvemshop(d.get("token"), d.get("loja")))
            if u.path == "/api/nuvemshop/aplicar":
                d = json.loads(self._corpo() or b"{}")
                _tarefa(aplicar_nuvemshop, d.get("modo", "zerar"), d.get("vids") or [], (d.get("quem") or "painel")[:40])
                return self._json({"ok": True})
            if u.path == "/api/categorias/aplicar":
                d = json.loads(self._corpo() or b"{}")
                return self._json(aplicar_sugestoes(con, d.get("itens") or [], (d.get("quem") or "painel")[:40],
                                                    d.get("confianca") or ""))
            if u.path == "/api/nuvemshop/categorias/ler":
                return self._json(ler_categorias(con))
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
            if u.path == "/api/importar/n99-catalogo":
                return self._json(importar_n99_catalogo(con, self._corpo()))
            if u.path == "/api/importar/ifood-catalogo":
                return self._json(importar_ifood_catalogo(con, self._corpo()))
            if u.path == "/api/importar/vendas":
                qp = {k: v[0] for k, v in parse_qs(u.query).items()}
                return self._json(importar_vendas(con, self._corpo(), qp.get("inicio"), qp.get("fim"),
                                                  unquote(qp.get("arquivo", "")),
                                                  unquote(self.headers.get("X-Quem") or "painel")[:40]))
            if u.path == "/api/importar/notas":
                qp = {k: v[0] for k, v in parse_qs(u.query).items()}
                corpo_arq = self._corpo()
                quem_arq = unquote(self.headers.get("X-Quem") or "painel")[:40]
                if corpo_arq[:4] == b"%PDF":
                    return self._json(importar_notas_pdf(con, corpo_arq, unquote(qp.get("arquivo", "")), quem_arq))
                return self._json(importar_notas(con, corpo_arq, unquote(qp.get("arquivo", "")), quem_arq))
            if u.path == "/api/cadastrar/trazer-nota":
                return self._json(trazer_itens_da_nota(con, unquote(self.headers.get("X-Quem") or "painel")[:40]))
            if u.path == "/api/entradas/auto-aplicar":
                return self._json(auto_vincular_notas(con, True))
            if u.path == "/api/entradas/auto-desfazer":
                return self._json(desfazer_auto_vinculos(con))
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
            if u.path == "/api/sync/pedidos":
                _tarefa(lambda prog: _sync_pedidos_tarefa(prog))
                return self._json({"ok": True})
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


def backup_extra():
    """Segunda cópia do banco FORA da pasta do painel (pendrive, HD externo ou pasta sincronizada com
    Google Drive/OneDrive), definida em config.json como "pasta_backup_extra". Guarda as 30 mais recentes.
    O resultado (ok, não configurado ou erro) vai para a tela inicial."""
    estado, detalhe = "nao_configurado", ""
    try:
        pasta = carregar_config().get("pasta_backup_extra", "").strip().strip('"')
        hoje = os.path.join(backups_dir(), f"banco_{datetime.now():%Y%m%d}.db")
        if pasta and os.path.exists(hoje):
            os.makedirs(pasta, exist_ok=True)
            destino = os.path.join(pasta, os.path.basename(hoje))
            if not os.path.exists(destino) or os.path.getmtime(destino) < os.path.getmtime(hoje):
                shutil.copy2(hoje, destino + ".tmp")
                os.replace(destino + ".tmp", destino)
            for n in sorted(x for x in os.listdir(pasta) if x.startswith("banco_") and x.endswith(".db"))[:-30]:
                os.remove(os.path.join(pasta, n))
            estado = "ok"
        elif pasta:
            estado, detalhe = "erro", "ainda não existe a cópia de hoje"
    except Exception as e:  # noqa: BLE001 - backup nunca derruba o painel
        estado, detalhe = "erro", str(e)[:120]
    try:
        con = conectar()
        set_meta(con, "backup_extra", estado)
        set_meta(con, "backup_extra_detalhe", detalhe)
        if estado == "ok":
            set_meta(con, "backup_extra_em", agora())
        con.commit()
        con.close()
    except Exception:  # noqa: BLE001
        pass


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
    backup_extra()


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
    threading.Thread(target=_rotina_pedidos, daemon=True).start()
    manter_acordado()
    if HOST not in ("127.0.0.1", "localhost") and not tem_senha():
        print("Para abrir o painel para outros aparelhos é obrigatório definir uma senha "
              "(PAINEL_SENHA ou \"senha\" no config.json).")
        return 1
    try:
        class Servidor(ThreadingHTTPServer):
            def handle_error(self, request, client_address):   # aba fechada no meio da resposta: não é erro
                if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
                    return
                super().handle_error(request, client_address)
        srv = Servidor((HOST, PORTA), Handler)
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
