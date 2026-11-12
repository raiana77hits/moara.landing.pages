#!/usr/bin/env python3
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
import csv
import hashlib
import hmac
import io
import json
import os
import secrets
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("PAINEL_DB", os.path.join(BASE, "banco.db"))
CONFIG_PATH = os.path.join(BASE, "config.json")
STATIC = os.path.join(BASE, "static")
PORTA = int(os.environ.get("PAINEL_PORTA") or os.environ.get("PORT") or "8765")
HOST = os.environ.get("PAINEL_HOST", "127.0.0.1")
SESSAO_DIAS = 30

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
    atualizado_em TEXT
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


def conectar():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
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
    url = f"https://api.tiendanube.com/v1/{cfg['loja']}/products?per_page=200&page={pagina}"
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


def sync_nuvemshop(con, progresso=None):
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
    por_sku = por_ean = criados = variacoes = 0
    for p in produtos:
        nome = _texto(p.get("name"))
        publicado = bool(p.get("published"))
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
}
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
    if grupo:
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


def listar(con, q="", filtro="todos", grupo="", pagina=1, por_pagina=50):
    where, args = _where(q, filtro, grupo)
    total = con.execute(f"SELECT COUNT(*) FROM produtos p WHERE {where}", args).fetchone()[0]
    linhas = [dict(r) for r in con.execute(
        f"SELECT p.* FROM produtos p WHERE {where} ORDER BY p.nome COLLATE NOCASE "
        "LIMIT ? OFFSET ?", args + [por_pagina, (pagina - 1) * por_pagina])]
    canais = _canais_de(con, [l["id"] for l in linhas])
    for l in linhas:
        l["canais"] = canais[l["id"]]
    return {"total": total, "pagina": pagina, "por_pagina": por_pagina, "itens": linhas}


def resumo(con):
    um = lambda sql: con.execute(sql).fetchone()[0]
    r = {"total": um("SELECT COUNT(*) FROM produtos"),
         "filtros": {k: um(f"SELECT COUNT(*) FROM produtos p WHERE {v}")
                     for k, v in FILTROS.items()},
         "grupos": [x[0] for x in con.execute(
             "SELECT DISTINCT grupo FROM produtos WHERE grupo<>'' ORDER BY grupo")],
         "meta": {x["chave"]: x["valor"] for x in con.execute("SELECT * FROM meta")},
         "nuvemshop_configurada": bool(carregar_config()["token"])}
    return r


def detalhe(con, pid):
    p = con.execute("SELECT * FROM produtos WHERE id=?", (pid,)).fetchone()
    if not p:
        return None
    d = dict(p)
    d["canais"] = _canais_de(con, [pid])[pid]
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
    if "obs" in dados:
        obs = (dados["obs"] or "").strip()
        if obs != (p["obs"] or ""):
            con.execute("UPDATE produtos SET obs=?, atualizado_em=? WHERE id=?",
                        (obs, agora(), pid))
            con.execute("INSERT INTO historico(produto_id,quando,campo,de,para,origem) "
                        "VALUES(?,?,?,?,?,?)", (pid, agora(), "obs", p["obs"] or "", obs, quem))
    con.commit()
    return detalhe(con, pid)


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


def exportar_csv(con, q, filtro, grupo):
    where, args = _where(q, filtro, grupo)
    saida = io.StringIO()
    w = csv.writer(saida, delimiter=";")
    w.writerow(["sku", "codigo_gestorz", "codigo_de_barras", "produto", "grupo", "preco",
                "estoque_oficial", "conferido_em", "nuvemshop", "ifood", "99food", "situacao", "obs"])
    linhas = con.execute(f"SELECT p.* FROM produtos p WHERE {where} "
                         "ORDER BY p.nome COLLATE NOCASE", args).fetchall()
    canais = _canais_de(con, [l["id"] for l in linhas]) if len(linhas) < 900 else None
    for l in linhas:
        cs = canais[l["id"]] if canais is not None else _canais_de(con, [l["id"]])[l["id"]]
        por = {c: "; ".join(f"{x['status']} ({x['estoque']})" if x["estoque"] else x["status"]
                            for x in cs if x["canal"] == c) for c in CANAIS}
        w.writerow([l["sku"] or "", l["gz_codigo"] or "", l["ean"] or "", l["nome"],
                    l["grupo"] or "", "" if l["preco"] is None else f"{l['preco']:.2f}".replace(".", ","),
                    "" if l["estoque"] is None else l["estoque"], l["conferido_em"] or "",
                    por["nuvemshop"], por["ifood"], por["99food"], l["situacao"] or "", l["obs"] or ""])
    return "﻿" + saida.getvalue()


# -------------------------------------------------------------------- servidor

TAREFA = {"rodando": False, "msg": "", "erro": "", "resultado": None}


def _rodar_sync():
    TAREFA.update(rodando=True, msg="Conectando na Nuvemshop", erro="", resultado=None)
    try:
        con = conectar()
        try:
            TAREFA["resultado"] = sync_nuvemshop(con, lambda m: TAREFA.update(msg=m))
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
                return self._json({"itens": conferidos_recentes(con),
                                   "total": con.execute("SELECT COUNT(*) FROM produtos WHERE "
                                                        "estoque IS NOT NULL").fetchone()[0]})
            if u.path == "/api/sem-cadastro":
                return self._json({"itens": [dict(r) for r in con.execute(
                    "SELECT * FROM leituras_sem_cadastro ORDER BY id DESC LIMIT 200")]})
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
            if u.path == "/api/sem-cadastro":
                return self._json(registrar_sem_cadastro(con, json.loads(self._corpo() or b"{}")))
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
    manter_acordado()
    if HOST not in ("127.0.0.1", "localhost") and not tem_senha():
        print("Para abrir o painel para outros aparelhos é obrigatório definir uma senha "
              "(PAINEL_SENHA ou \"senha\" no config.json).")
        return 1
    srv = ThreadingHTTPServer((HOST, PORTA), Handler)
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
