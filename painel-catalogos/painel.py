#!/usr/bin/env python3
"""Painel de Catálogos — compara a lista da loja com o iFood, o Anota Aí e a 99Food.

Só a biblioteca padrão do Python 3. Nada para instalar.
Não fala com nenhuma loja: só lê planilhas que você exporta e mostra o que está diferente.
"""

import csv
import io
import json
import math
import os
import re
import sqlite3
import sys
import threading
import unicodedata
import webbrowser
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PASTA = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(PASTA, "static")
BANCO = os.environ.get("PAINEL_DB") or os.path.join(PASTA, "banco.db")
ARQ_MARCA = os.environ.get("PAINEL_MARCA") or os.path.join(PASTA, "marca.json")

MARCA_PADRAO = {
    "nome": "Painel de Catálogos",
    "porta": 8767,
    "cor": "#1e3fae",
    "cor_escura": "#182549",
    "divisor_entrega": 0.70,
    "arredondamento": 0.01,
    "tolerancia_preco": 0.05,
    "canais": ["ifood", "anota", "n99"],
    "cliente_inicial": "Vibe Pet",
}

CANAIS = {
    "ifood": {"nome": "iFood", "artigo": "do", "entrega": True},
    "anota": {"nome": "Anota Aí", "artigo": "do", "entrega": False},
    "n99": {"nome": "99Food", "artigo": "da", "entrega": True},
}

LIMITE_UPLOAD = 20_000_000          # 20 MB; planilha de catálogo não passa nem perto
MAX_LINHAS_TELA = 500               # o limite aparece na tela, nunca corta em silêncio


# ----------------------------------------------------------------- utilidades

def agora():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def hoje():
    return datetime.now().strftime("%Y-%m-%d")


def carregar_marca():
    marca = dict(MARCA_PADRAO)
    try:
        with open(ARQ_MARCA, encoding="utf-8") as f:
            marca.update(json.load(f))
    except FileNotFoundError:
        pass
    except (ValueError, OSError) as e:
        print(f"Aviso: não consegui ler {ARQ_MARCA} ({e}). Usando os valores padrão.")
    if os.environ.get("PAINEL_PORTA"):
        marca["porta"] = int(os.environ["PAINEL_PORTA"])
    marca["canais"] = [c for c in marca.get("canais") or [] if c in CANAIS] or list(CANAIS)
    return marca


MARCA = carregar_marca()


def norm_ean(v):
    """Só os dígitos, sem zeros à esquerda. EAN curto demais não vale como código."""
    v = "".join(ch for ch in str(v or "") if ch.isdigit())
    v = v.lstrip("0")
    return v if len(v) >= 8 else ""


def norm_nome(v):
    """Nome sem acento, sem pontuação e sem espaço dobrado, para comparar."""
    v = unicodedata.normalize("NFKD", str(v or "")).encode("ascii", "ignore").decode()
    v = re.sub(r"[^a-z0-9 ]+", " ", v.lower())
    return re.sub(r"\s+", " ", v).strip()


def norm_codigo(v):
    return re.sub(r"\s+", "", str(v or "")).upper()


def num(v):
    """Número de uma célula de planilha. Aceita 'R$ 1.234,56' e '1234.56'."""
    if v is None:
        return None
    t = str(v).strip()
    if not t:
        return None
    t = re.sub(r"[^0-9,.\-]", "", t)
    if not t or t in {"-", ".", ","}:
        return None
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") else t.replace(",", "")
    elif "," in t:
        t = t.replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def inteiro(v):
    n = num(v)
    return None if n is None else int(round(n))


def arredonda(valor, passo):
    """Arredonda para cima no passo pedido (0,01 = centavo; 0,10; 1,00)."""
    if valor is None:
        return None
    if not passo or passo <= 0:
        return round(valor, 2)
    return round(math.ceil(round(valor / passo, 6)) * passo, 2)


def preco_esperado(canal, preco_loja):
    """Quanto deveria custar no canal. Entrega = preço da loja / divisor."""
    if preco_loja is None or preco_loja <= 0:
        return None
    if CANAIS.get(canal, {}).get("entrega"):
        divisor = float(MARCA.get("divisor_entrega") or 0.70)
        if divisor <= 0:
            return None
        return arredonda(preco_loja / divisor, float(MARCA.get("arredondamento") or 0.01))
    return round(preco_loja, 2)


def reais(v):
    if v is None:
        return ""
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# ------------------------------------------------------------- banco de dados

ESQUEMA = """
CREATE TABLE IF NOT EXISTS clientes (
  id INTEGER PRIMARY KEY,
  nome TEXT NOT NULL UNIQUE,
  cor TEXT,
  criado_em TEXT
);

-- a lista mestra: o que a loja vende, com o preço que vale
CREATE TABLE IF NOT EXISTS produtos (
  id INTEGER PRIMARY KEY,
  cliente_id INTEGER NOT NULL,
  ean TEXT, ean_norm TEXT,
  codigo TEXT,
  nome TEXT, nome_norm TEXT,
  marca TEXT, categoria TEXT, un TEXT,
  preco_loja REAL,
  estoque REAL,
  granel INTEGER DEFAULT 0,
  tem_foto INTEGER DEFAULT 0,
  obs TEXT,
  criado_em TEXT, atualizado_em TEXT
);
CREATE INDEX IF NOT EXISTS ix_prod_cli ON produtos(cliente_id);
CREATE INDEX IF NOT EXISTS ix_prod_ean ON produtos(cliente_id, ean_norm);
CREATE INDEX IF NOT EXISTS ix_prod_nome ON produtos(cliente_id, nome_norm);

-- uma linha por produto por canal, vinda da exportação daquele canal
CREATE TABLE IF NOT EXISTS canais (
  id INTEGER PRIMARY KEY,
  cliente_id INTEGER NOT NULL,
  canal TEXT NOT NULL,
  produto_id INTEGER,
  ean TEXT, ean_norm TEXT,
  codigo TEXT,
  nome TEXT, nome_norm TEXT,
  preco REAL,
  status TEXT,
  categoria TEXT,
  estoque REAL,
  tem_foto INTEGER DEFAULT 0,
  importacao_id INTEGER,
  atualizado_em TEXT
);
CREATE INDEX IF NOT EXISTS ix_can_cli ON canais(cliente_id, canal);
CREATE INDEX IF NOT EXISTS ix_can_prod ON canais(produto_id);

-- ligação feita à mão: código do canal (ex.: SM… do iFood) -> produto
CREATE TABLE IF NOT EXISTS de_para (
  id INTEGER PRIMARY KEY,
  cliente_id INTEGER NOT NULL,
  canal TEXT NOT NULL,
  chave TEXT NOT NULL,
  produto_id INTEGER NOT NULL,
  quando TEXT,
  UNIQUE(cliente_id, canal, chave)
);

CREATE TABLE IF NOT EXISTS importacoes (
  id INTEGER PRIMARY KEY,
  cliente_id INTEGER NOT NULL,
  tipo TEXT, arquivo TEXT,
  linhas INTEGER, novos INTEGER, ligados INTEGER, soltos INTEGER,
  mapa TEXT, quando TEXT
);

-- o que já foi resolvido, para a lista não repetir o que você fez
CREATE TABLE IF NOT EXISTS tarefas (
  id INTEGER PRIMARY KEY,
  cliente_id INTEGER NOT NULL,
  tipo TEXT NOT NULL,
  chave TEXT NOT NULL,
  feito_em TEXT,
  UNIQUE(cliente_id, tipo, chave)
);

CREATE TABLE IF NOT EXISTS historico (
  id INTEGER PRIMARY KEY,
  cliente_id INTEGER, produto_id INTEGER,
  texto TEXT, quando TEXT
);

CREATE TABLE IF NOT EXISTS meta (
  cliente_id INTEGER NOT NULL, chave TEXT NOT NULL, valor TEXT,
  UNIQUE(cliente_id, chave)
);
"""


def conectar():
    novo = not os.path.exists(BANCO)
    con = sqlite3.connect(BANCO, timeout=20)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(ESQUEMA)
    if novo or not con.execute("SELECT 1 FROM clientes LIMIT 1").fetchone():
        con.execute("INSERT OR IGNORE INTO clientes(nome,cor,criado_em) VALUES(?,?,?)",
                    (MARCA.get("cliente_inicial") or "Cliente 1", MARCA.get("cor"), agora()))
    con.commit()
    return con


def cliente_atual(con, pedido=None):
    if pedido:
        r = con.execute("SELECT * FROM clientes WHERE id=?", (pedido,)).fetchone()
        if r:
            return r
    r = con.execute("SELECT * FROM clientes ORDER BY id LIMIT 1").fetchone()
    if not r:
        con.execute("INSERT INTO clientes(nome,cor,criado_em) VALUES(?,?,?)",
                    ("Cliente 1", MARCA.get("cor"), agora()))
        con.commit()
        r = con.execute("SELECT * FROM clientes ORDER BY id LIMIT 1").fetchone()
    return r


def set_meta(con, cliente_id, chave, valor):
    con.execute("INSERT INTO meta(cliente_id,chave,valor) VALUES(?,?,?) "
                "ON CONFLICT(cliente_id,chave) DO UPDATE SET valor=excluded.valor",
                (cliente_id, chave, str(valor)))


def pegar_meta(con, cliente_id):
    return {r["chave"]: r["valor"] for r in
            con.execute("SELECT chave,valor FROM meta WHERE cliente_id=?", (cliente_id,))}


def anotar(con, cliente_id, produto_id, texto):
    con.execute("INSERT INTO historico(cliente_id,produto_id,texto,quando) VALUES(?,?,?,?)",
                (cliente_id, produto_id, texto, agora()))


# ------------------------------------------------------- leitura de planilhas

def ler_csv_tabela(dados):
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            texto = dados.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("Não consegui ler o arquivo. Salve como CSV ou XLSX e tente de novo.")
    amostra = texto[:8000]
    delim = ";" if amostra.count(";") > amostra.count(",") else ","
    if amostra.count("\t") > max(amostra.count(";"), amostra.count(",")):
        delim = "\t"
    return [list(l) for l in csv.reader(io.StringIO(texto), delimiter=delim)]


def ler_xlsx_tabela(raw):
    """Primeira aba com conteúdo de um .xlsx, como lista de listas.

    Tolera o que outros sistemas fazem de errado: inlineStr e referências de
    coluna fora do padrão (ex.: '{1' em vez de 'AA1').
    """
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise ValueError("Isso não parece uma planilha .xlsx.")
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    textos = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(ns + "si"):
            textos.append("".join(t.text or "" for t in si.iter(ns + "t")))
    abas = sorted(n for n in z.namelist()
                  if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"))
    if not abas:
        raise ValueError("Isso não parece uma planilha .xlsx.")

    def ler_aba(caminho):
        linhas = []
        for row in ET.fromstring(z.read(caminho)).iter(ns + "row"):
            d = {}
            for c in row.findall(ns + "c"):
                m = re.match(r"[A-Za-z]+", c.get("r") or "A")
                letras = m.group(0) if m else "A"
                col = 0
                for ch in letras.upper():
                    col = col * 26 + ord(ch) - 64
                if col < 1:
                    continue
                v, t = c.find(ns + "v"), c.get("t")
                if t == "s" and v is not None and v.text is not None:
                    try:
                        val = textos[int(v.text)]
                    except (ValueError, IndexError):
                        val = v.text
                elif t == "inlineStr":
                    val = "".join(x.text or "" for x in c.iter(ns + "t"))
                else:
                    val = v.text if v is not None else None
                d[col - 1] = val
            linhas.append([d.get(i) for i in range(max(d) + 1)] if d else [])
        return linhas

    melhor = []
    for caminho in abas:
        try:
            linhas = ler_aba(caminho)
        except (KeyError, ET.ParseError):
            continue
        cheias = [l for l in linhas if any(str(c or "").strip() for c in l)]
        if len(cheias) > len(melhor):
            melhor = cheias
        if len(melhor) > 1:
            break
    return melhor


def ler_tabela(raw):
    """CSV ou XLSX, detectado pelo conteúdo. Devolve (cabecalho, linhas)."""
    if not raw:
        raise ValueError("O arquivo chegou vazio.")
    linhas = ler_xlsx_tabela(raw) if raw[:2] == b"PK" else ler_csv_tabela(raw)
    linhas = [l for l in linhas if any(str(c or "").strip() for c in l)]
    if not linhas:
        raise ValueError("A planilha está vazia.")
    # o cabeçalho é a primeira linha com pelo menos duas células preenchidas
    inicio = 0
    for i, l in enumerate(linhas[:15]):
        if sum(1 for c in l if str(c or "").strip()) >= 2:
            inicio = i
            break
    cab = [str(c or "").strip() for c in linhas[inicio]]
    corpo = linhas[inicio + 1:]
    largura = max([len(cab)] + [len(l) for l in corpo[:200]]) if corpo else len(cab)
    cab = (cab + [""] * largura)[:largura]
    cab = [c or f"Coluna {i + 1}" for i, c in enumerate(cab)]
    return cab, [(list(l) + [None] * largura)[:largura] for l in corpo]


# ------------------------------------------- adivinhar as colunas da planilha

CAMPOS = {
    "loja": [
        ("ean", "Código de barras (EAN)", True),
        ("nome", "Nome do produto", True),
        ("preco_loja", "Preço na loja", False),
        ("codigo", "Código interno", False),
        ("marca", "Marca", False),
        ("categoria", "Categoria", False),
        ("un", "Unidade", False),
        ("estoque", "Estoque", False),
    ],
    "canal": [
        ("ean", "Código de barras (EAN)", False),
        ("codigo", "Código no canal", False),
        ("nome", "Nome no canal", True),
        ("preco", "Preço no canal", False),
        ("status", "Situação", False),
        ("categoria", "Categoria no canal", False),
        ("estoque", "Estoque", False),
        ("foto", "Foto", False),
    ],
    "fotos": [
        ("arquivo", "Nome do arquivo", True),
    ],
}

SINONIMOS = {
    "ean": ["ean", "ean do produto", "codigo de barras", "cod barras", "codbarras", "gtin",
            "barcode", "cod de barras", "ean13", "codigo barras"],
    "codigo": ["codigo", "cod", "sku", "plu", "codigo interno", "id produto", "id do produto",
               "codigo do produto", "referencia", "ref", "codigo externo", "id"],
    "nome": ["nome", "nome do produto", "descricao", "produto", "item", "titulo", "descricao do produto"],
    "preco_loja": ["preco", "preco de venda", "venda", "preco loja", "valor", "preco unitario",
                   "preco atual", "preco da loja", "preco varejo"],
    "preco": ["preco", "preco de venda", "venda", "valor", "preco unitario", "preco no ifood",
              "preco atual", "preco promocional", "preco final"],
    "status": ["status", "situacao", "em venda", "estado", "ativo", "disponivel", "situacao do item"],
    "categoria": ["categoria", "grupo", "departamento", "secao", "categoria do produto", "tipo"],
    "marca": ["marca", "fabricante", "brand"],
    "un": ["un", "unidade", "unidade de medida", "medida", "embalagem"],
    "estoque": ["estoque", "qtd", "quantidade", "saldo", "estoque atual", "inventario", "qtde"],
    "foto": ["foto", "imagem", "url da imagem", "url da foto", "tem foto", "imagem url", "link da foto"],
    "arquivo": ["arquivo", "nome do arquivo", "nome", "filename", "file", "foto"],
}


def _chave_cab(t):
    t = unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", t.lower())).strip()


def _combina(chave, alvos, exato):
    """Compara o título da coluna com os apelidos de um campo.

    Fora do modo exato compara por palavra inteira, e só aceita pedaço de
    palavra com apelidos de 4 letras ou mais: senão "id" casaria dentro de
    "unidade" e "cod" dentro de qualquer coisa.
    """
    if exato:
        return chave in alvos
    palavras = set(chave.split())
    for a in alvos:
        if a in palavras:
            return True
        if len(a) >= 4 and (a in chave or chave in a):
            return True
    return False


def sugerir_mapa(cab, grupo):
    """Para cada campo, qual coluna da planilha parece ser. -1 = nenhuma."""
    chaves = [_chave_cab(c) for c in cab]
    usadas = set()
    mapa = {}
    for campo, _rotulo, _obrig in CAMPOS[grupo]:
        achou = -1
        alvos = SINONIMOS.get(campo, [campo])
        for exato in (True, False):
            for i, ch in enumerate(chaves):
                if i in usadas or not ch:
                    continue
                if _combina(ch, alvos, exato):
                    achou = i
                    break
            if achou >= 0:
                break
        if achou < 0 and grupo == "fotos" and len(cab) == 1 and 0 not in usadas:
            # lista de fotos feita com "dir /b": uma coluna só e sem cabeçalho
            achou = 0
        if achou >= 0:
            usadas.add(achou)
        mapa[campo] = achou
    return mapa


def _pegar(linha, mapa, campo):
    i = mapa.get(campo, -1)
    if i is None or i < 0 or i >= len(linha):
        return None
    v = linha[i]
    return None if v is None or str(v).strip() == "" else str(v).strip()


# ----------------------------------------------------------------- importação

def _ligar_produto(con, cliente_id, canal, ean, codigo, nome_norm):
    """Acha o produto da lista mestra para uma linha de canal."""
    chave = norm_codigo(codigo) or norm_ean(ean)
    if chave:
        r = con.execute("SELECT produto_id FROM de_para WHERE cliente_id=? AND canal=? AND chave=?",
                        (cliente_id, canal, chave)).fetchone()
        if r:
            if con.execute("SELECT 1 FROM produtos WHERE id=?", (r["produto_id"],)).fetchone():
                return r["produto_id"], "de_para"
    e = norm_ean(ean)
    if e:
        r = con.execute("SELECT id FROM produtos WHERE cliente_id=? AND ean_norm=? ORDER BY id LIMIT 1",
                        (cliente_id, e)).fetchone()
        if r:
            return r["id"], "ean"
    c = norm_codigo(codigo)
    if c:
        r = con.execute("SELECT id FROM produtos WHERE cliente_id=? AND UPPER(codigo)=? ORDER BY id LIMIT 1",
                        (cliente_id, c)).fetchone()
        if r:
            return r["id"], "codigo"
    if nome_norm:
        achados = con.execute(
            "SELECT id FROM produtos WHERE cliente_id=? AND nome_norm=? LIMIT 2",
            (cliente_id, nome_norm)).fetchall()
        if len(achados) == 1:
            return achados[0]["id"], "nome"
    return None, ""


def importar_loja(con, cliente_id, cab, linhas, mapa, arquivo):
    """A lista da loja: cria e atualiza a lista mestra de produtos."""
    novos = atualizados = ignorados = 0
    quando = agora()
    for linha in linhas:
        nome = _pegar(linha, mapa, "nome")
        ean = _pegar(linha, mapa, "ean")
        codigo = _pegar(linha, mapa, "codigo")
        if not nome and not ean and not codigo:
            ignorados += 1
            continue
        e = norm_ean(ean)
        nn = norm_nome(nome)
        achado = None
        if e:
            achado = con.execute("SELECT id FROM produtos WHERE cliente_id=? AND ean_norm=? LIMIT 1",
                                 (cliente_id, e)).fetchone()
        if not achado and codigo:
            achado = con.execute(
                "SELECT id FROM produtos WHERE cliente_id=? AND UPPER(codigo)=? LIMIT 1",
                (cliente_id, norm_codigo(codigo))).fetchone()
        if not achado and nn:
            # casa pelo nome, mas nunca junta duas linhas com códigos de barras
            # diferentes: isso apagaria um EAN sem avisar. Ficam dois produtos,
            # e a lista "Repetido na lista da loja" mostra o caso.
            for cand in con.execute(
                    "SELECT id, ean_norm FROM produtos WHERE cliente_id=? AND nome_norm=? ORDER BY id",
                    (cliente_id, nn)):
                if not e or not cand["ean_norm"] or cand["ean_norm"] == e:
                    achado = cand
                    break
        campos = {
            "ean": ean, "ean_norm": e, "codigo": codigo, "nome": nome, "nome_norm": nn,
            "marca": _pegar(linha, mapa, "marca"), "categoria": _pegar(linha, mapa, "categoria"),
            "un": _pegar(linha, mapa, "un"), "preco_loja": num(_pegar(linha, mapa, "preco_loja")),
            "estoque": num(_pegar(linha, mapa, "estoque")),
        }
        un = (campos["un"] or "").lower()
        campos["granel"] = 1 if un in {"kg", "g", "gr", "grama", "quilo", "granel", "lt", "l"} else 0
        if achado:
            antes = con.execute("SELECT * FROM produtos WHERE id=?", (achado["id"],)).fetchone()
            sets, vals = [], []
            for k, v in campos.items():
                if v is None or v == "":
                    continue
                sets.append(f"{k}=?")
                vals.append(v)
            if sets:
                sets.append("atualizado_em=?")
                vals.extend([quando, achado["id"]])
                con.execute(f"UPDATE produtos SET {', '.join(sets)} WHERE id=?", vals)
            if campos["preco_loja"] is not None and antes["preco_loja"] is not None \
                    and abs(campos["preco_loja"] - antes["preco_loja"]) > 0.001:
                anotar(con, cliente_id, achado["id"],
                       f"Preço da loja: {reais(antes['preco_loja'])} -> {reais(campos['preco_loja'])}")
            atualizados += 1
        else:
            campos["criado_em"] = quando
            campos["atualizado_em"] = quando
            campos["cliente_id"] = cliente_id
            cols = ", ".join(campos)
            con.execute(f"INSERT INTO produtos({cols}) VALUES({', '.join('?' * len(campos))})",
                        list(campos.values()))
            novos += 1
    set_meta(con, cliente_id, "importado_loja", quando)
    return {"novos": novos, "atualizados": atualizados, "ignorados": ignorados,
            "ligados": 0, "soltos": 0}


def importar_canal(con, cliente_id, canal, cab, linhas, mapa, arquivo, imp_id):
    """A exportação de um canal. Substitui o que havia daquele canal."""
    con.execute("DELETE FROM canais WHERE cliente_id=? AND canal=?", (cliente_id, canal))
    ligados = soltos = ignorados = 0
    quando = agora()
    for linha in linhas:
        nome = _pegar(linha, mapa, "nome")
        ean = _pegar(linha, mapa, "ean")
        codigo = _pegar(linha, mapa, "codigo")
        if not nome and not ean and not codigo:
            ignorados += 1
            continue
        nn = norm_nome(nome)
        pid, _como = _ligar_produto(con, cliente_id, canal, ean, codigo, nn)
        if pid:
            ligados += 1
        else:
            soltos += 1
        foto = _pegar(linha, mapa, "foto")
        tem_foto = 0
        if foto:
            baixo = foto.strip().lower()
            tem_foto = 0 if baixo in {"nao", "não", "0", "false", "sem foto", "-"} else 1
        con.execute(
            "INSERT INTO canais(cliente_id,canal,produto_id,ean,ean_norm,codigo,nome,nome_norm,"
            "preco,status,categoria,estoque,tem_foto,importacao_id,atualizado_em) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cliente_id, canal, pid, ean, norm_ean(ean), codigo, nome, nn,
             num(_pegar(linha, mapa, "preco")), _pegar(linha, mapa, "status"),
             _pegar(linha, mapa, "categoria"), num(_pegar(linha, mapa, "estoque")),
             tem_foto, imp_id, quando))
    set_meta(con, cliente_id, f"importado_{canal}", quando)
    return {"novos": 0, "atualizados": 0, "ignorados": ignorados,
            "ligados": ligados, "soltos": soltos}


def _parece_arquivo(v):
    """'7891000100103.jpg' e '7891000100103' parecem nome de foto; 'Nome do arquivo' não."""
    t = str(v or "").strip()
    if not t:
        return False
    base, ext = os.path.splitext(t)
    if ext and re.fullmatch(r"\.[A-Za-z0-9]{2,5}", ext):
        return True
    return base.isdigit()


def importar_fotos(con, cliente_id, cab, linhas, mapa, arquivo):
    """Lista de nomes de arquivo do Drive. O nome do arquivo é o EAN."""
    con.execute("UPDATE produtos SET tem_foto=0 WHERE cliente_id=?", (cliente_id,))
    achados = sem_dono = 0
    # uma lista feita com "dir /b" não tem cabeçalho: a primeira linha já é uma foto
    i = mapa.get("arquivo", -1)
    if 0 <= i < len(cab) and _parece_arquivo(cab[i]):
        linhas = [list(cab)] + list(linhas)
    for linha in linhas:
        nome_arq = _pegar(linha, mapa, "arquivo")
        if not nome_arq:
            continue
        base = os.path.basename(str(nome_arq))
        e = norm_ean(os.path.splitext(base)[0])
        if not e:
            sem_dono += 1
            continue
        r = con.execute("UPDATE produtos SET tem_foto=1 WHERE cliente_id=? AND ean_norm=?",
                        (cliente_id, e))
        if r.rowcount:
            achados += 1
        else:
            sem_dono += 1
    set_meta(con, cliente_id, "importado_fotos", agora())
    return {"novos": 0, "atualizados": achados, "ignorados": sem_dono,
            "ligados": achados, "soltos": sem_dono}


def importar(con, cliente_id, tipo, raw, mapa, arquivo):
    cab, linhas = ler_tabela(raw)
    if not linhas:
        raise ValueError("A planilha tem cabeçalho mas nenhuma linha de produto.")
    grupo = "loja" if tipo == "loja" else ("fotos" if tipo == "fotos" else "canal")
    for campo, rotulo, obrig in CAMPOS[grupo]:
        if obrig and mapa.get(campo, -1) < 0:
            raise ValueError(f"Falta dizer qual coluna é \"{rotulo}\".")
    cur = con.execute(
        "INSERT INTO importacoes(cliente_id,tipo,arquivo,linhas,mapa,quando) VALUES(?,?,?,?,?,?)",
        (cliente_id, tipo, arquivo, len(linhas), json.dumps(mapa), agora()))
    imp_id = cur.lastrowid
    if tipo == "loja":
        r = importar_loja(con, cliente_id, cab, linhas, mapa, arquivo)
        religar_tudo(con, cliente_id)
    elif tipo == "fotos":
        r = importar_fotos(con, cliente_id, cab, linhas, mapa, arquivo)
    else:
        r = importar_canal(con, cliente_id, tipo, cab, linhas, mapa, arquivo, imp_id)
    con.execute("UPDATE importacoes SET novos=?, ligados=?, soltos=? WHERE id=?",
                (r["novos"], r["ligados"], r["soltos"], imp_id))
    con.commit()
    r["linhas"] = len(linhas)
    r["tipo"] = tipo
    return r


def religar_tudo(con, cliente_id):
    """Depois de mudar a lista da loja, tenta ligar de novo o que estava solto."""
    for canal in MARCA["canais"]:
        for l in con.execute("SELECT * FROM canais WHERE cliente_id=? AND canal=? AND produto_id IS NULL",
                             (cliente_id, canal)).fetchall():
            pid, _ = _ligar_produto(con, cliente_id, canal, l["ean"], l["codigo"], l["nome_norm"])
            if pid:
                con.execute("UPDATE canais SET produto_id=? WHERE id=?", (pid, l["id"]))
    con.commit()


# ---------------------------------------------------- o que está diferente

PARADO = ("recusad", "reprovad", "inativ", "pausad", "desativ", "esgotad", "indisponivel",
          "cadastrar", "analise", "pendente", "revisao", "bloquead", "rascunho", "oculto")
PARADO_EXATO = {"nao", "n", "false", "0", "no", "sem venda", "fora", "nao vendendo"}


def vendendo(status):
    """A linha do canal está no ar? Sem coluna de situação, assume que sim."""
    if status is None or str(status).strip() == "":
        return True
    s = norm_nome(status)
    if s in PARADO_EXATO:
        return False
    return not any(p in s for p in PARADO)


TIPOS = {}


def tipo_lista(chave, titulo, explica, canal=None):
    def dec(fn):
        TIPOS[chave] = {"chave": chave, "titulo": titulo, "explica": explica,
                        "canal": canal, "fn": fn}
        return fn
    return dec


def _produtos(con, cid):
    return con.execute("SELECT * FROM produtos WHERE cliente_id=? ORDER BY nome", (cid,)).fetchall()


def _mapa_canal(con, cid, canal):
    """produto_id -> linha do canal (a que está vendendo, se houver)."""
    saida = {}
    for l in con.execute("SELECT * FROM canais WHERE cliente_id=? AND canal=? AND produto_id IS NOT NULL",
                         (cid, canal)):
        atual = saida.get(l["produto_id"])
        if atual is None or (vendendo(l["status"]) and not vendendo(atual["status"])):
            saida[l["produto_id"]] = l
    return saida


def _item(p, extra=None):
    d = {"produto_id": p["id"], "ean": p["ean"] or "", "codigo": p["codigo"] or "",
         "nome": p["nome"] or "", "marca": p["marca"] or "", "categoria": p["categoria"] or "",
         "preco_loja": p["preco_loja"], "granel": bool(p["granel"]), "un": p["un"] or ""}
    if extra:
        d.update(extra)
    return d


@tipo_lista("falta_ifood", "Falta cadastrar no iFood",
            "Produtos da lista da loja que não aparecem no iFood, ou que estão lá sem vender.", "ifood")
def _falta_ifood(con, cid):
    return _falta_canal(con, cid, "ifood")


@tipo_lista("falta_anota", "Falta cadastrar no Anota Aí",
            "Produtos da lista da loja que não aparecem no Anota Aí.", "anota")
def _falta_anota(con, cid):
    return _falta_canal(con, cid, "anota")


@tipo_lista("falta_n99", "Falta cadastrar na 99Food",
            "Produtos da lista da loja que não aparecem na 99Food.", "n99")
def _falta_n99(con, cid):
    return _falta_canal(con, cid, "n99")


def canal_vazio(con, cid, canal):
    """O canal ainda não teve planilha importada."""
    return not con.execute("SELECT 1 FROM canais WHERE cliente_id=? AND canal=? LIMIT 1",
                           (cid, canal)).fetchone()


def _falta_canal(con, cid, canal):
    # sem planilha do canal não dá para dizer que falta algo: seria a lista toda
    if canal_vazio(con, cid, canal):
        return []
    no_canal = _mapa_canal(con, cid, canal)
    saida = []
    for p in _produtos(con, cid):
        l = no_canal.get(p["id"])
        if l is None:
            saida.append(_item(p, {"chave": f"{canal}:{p['id']}", "situacao": "não está no canal"}))
        elif not vendendo(l["status"]):
            saida.append(_item(p, {"chave": f"{canal}:{p['id']}",
                                   "situacao": l["status"] or "não está vendendo",
                                   "nome_canal": l["nome"] or ""}))
    return saida


@tipo_lista("preco_ifood", "Preço diferente no iFood",
            "O preço do iFood deveria ser o preço da loja dividido pelo divisor.", "ifood")
def _preco_ifood(con, cid):
    return _preco_canal(con, cid, "ifood")


@tipo_lista("preco_anota", "Preço diferente no Anota Aí",
            "No Anota Aí o preço deveria ser igual ao preço da loja.", "anota")
def _preco_anota(con, cid):
    return _preco_canal(con, cid, "anota")


@tipo_lista("preco_n99", "Preço diferente na 99Food",
            "O preço da 99Food deveria ser o preço da loja dividido pelo divisor.", "n99")
def _preco_n99(con, cid):
    return _preco_canal(con, cid, "n99")


def _preco_canal(con, cid, canal):
    tol = float(MARCA.get("tolerancia_preco") or 0.05)
    no_canal = _mapa_canal(con, cid, canal)
    saida = []
    for p in _produtos(con, cid):
        l = no_canal.get(p["id"])
        if l is None or p["preco_loja"] is None or p["preco_loja"] <= 0:
            continue
        esperado = preco_esperado(canal, p["preco_loja"])
        if esperado is None:
            continue
        if l["preco"] is None:
            saida.append(_item(p, {"chave": f"preco:{canal}:{p['id']}", "preco_canal": None,
                                   "esperado": esperado, "diferenca": None,
                                   "situacao": "sem preço no canal"}))
            continue
        dif = round(l["preco"] - esperado, 2)
        if abs(dif) > tol + 0.0001:
            saida.append(_item(p, {"chave": f"preco:{canal}:{p['id']}", "preco_canal": l["preco"],
                                   "esperado": esperado, "diferenca": dif,
                                   "situacao": "mais caro" if dif > 0 else "mais barato"}))
    saida.sort(key=lambda d: -abs(d.get("diferenca") or 0))
    return saida


@tipo_lista("sem_foto", "Sem foto",
            "Produtos sem foto na pasta do Drive e/ou sem foto no canal.")
def _sem_foto(con, cid):
    fotos_canal = {}
    for canal in MARCA["canais"]:
        for pid, l in _mapa_canal(con, cid, canal).items():
            if l["tem_foto"]:
                fotos_canal.setdefault(pid, []).append(CANAIS[canal]["nome"])
    saida = []
    for p in _produtos(con, cid):
        tem_drive = bool(p["tem_foto"])
        nos_canais = fotos_canal.get(p["id"]) or []
        if tem_drive and nos_canais:
            continue
        falta = []
        if not tem_drive:
            falta.append("no Drive")
        if not nos_canais:
            falta.append("nos canais")
        saida.append(_item(p, {"chave": f"foto:{p['id']}", "situacao": "falta foto " + " e ".join(falta),
                               "fotos_em": ", ".join(nos_canais)}))
    return saida


@tipo_lista("sem_ean", "Sem código de barras",
            "Sem EAN não dá para ligar o produto entre a loja e os canais.")
def _sem_ean(con, cid):
    return [_item(p, {"chave": f"ean:{p['id']}", "situacao": "sem EAN"})
            for p in _produtos(con, cid) if not p["ean_norm"]]


@tipo_lista("sem_nome", "Sem nome",
            "Linhas da lista da loja que vieram sem descrição. Dá para escrever o nome na ficha.")
def _sem_nome(con, cid):
    return [_item(p, {"chave": f"nome:{p['id']}",
                      "situacao": "sem nome" + (f" · EAN {p['ean']}" if p["ean"] else "")})
            for p in _produtos(con, cid) if not (p["nome"] or "").strip()]


@tipo_lista("sem_preco", "Sem preço na loja",
            "Sem o preço da loja o painel não consegue conferir o preço dos canais.")
def _sem_preco(con, cid):
    return [_item(p, {"chave": f"semp:{p['id']}", "situacao": "sem preço"})
            for p in _produtos(con, cid) if p["preco_loja"] is None or p["preco_loja"] <= 0]


@tipo_lista("duplicado", "Repetido na lista da loja",
            "O mesmo código de barras ou o mesmo nome em mais de uma linha.")
def _duplicado(con, cid):
    saida = []
    for col, rotulo in (("ean_norm", "código de barras"), ("nome_norm", "nome")):
        for r in con.execute(
                f"SELECT {col} AS v, COUNT(*) AS n FROM produtos WHERE cliente_id=? AND {col} IS NOT NULL "
                f"AND {col}<>'' GROUP BY {col} HAVING n>1", (cid,)):
            for p in con.execute(f"SELECT * FROM produtos WHERE cliente_id=? AND {col}=? ORDER BY id",
                                 (cid, r["v"])):
                saida.append(_item(p, {"chave": f"dup:{col}:{r['v']}:{p['id']}",
                                       "situacao": f"{r['n']} linhas com o mesmo {rotulo}"}))
    return saida


@tipo_lista("status_ifood", "Parado no iFood",
            "Linhas do iFood em análise, recusadas ou esperando cadastro.", "ifood")
def _status_ifood(con, cid):
    saida = []
    for l in con.execute("SELECT * FROM canais WHERE cliente_id=? AND canal='ifood' ORDER BY nome", (cid,)):
        if vendendo(l["status"]):
            continue
        p = con.execute("SELECT * FROM produtos WHERE id=?", (l["produto_id"],)).fetchone() \
            if l["produto_id"] else None
        base = _item(p) if p else {"produto_id": None, "ean": l["ean"] or "", "codigo": l["codigo"] or "",
                                   "nome": l["nome"] or "", "marca": "", "categoria": "",
                                   "preco_loja": None, "granel": False, "un": ""}
        base.update({"chave": f"st:ifood:{l['id']}", "situacao": l["status"] or "parado",
                     "nome_canal": l["nome"] or "", "preco_canal": l["preco"]})
        saida.append(base)
    return saida


@tipo_lista("soltos", "Linhas de canal sem produto",
            "Linhas que estão no canal mas o painel não achou na lista da loja. Ligue à mão em De-para.")
def _soltos(con, cid):
    saida = []
    for l in con.execute("SELECT * FROM canais WHERE cliente_id=? AND produto_id IS NULL ORDER BY canal, nome",
                         (cid,)):
        saida.append({"produto_id": None, "canal": l["canal"],
                      "canal_nome": CANAIS.get(l["canal"], {}).get("nome", l["canal"]),
                      "canal_linha_id": l["id"], "ean": l["ean"] or "", "codigo": l["codigo"] or "",
                      "nome": l["nome"] or "", "marca": "", "categoria": l["categoria"] or "",
                      "preco_loja": None, "preco_canal": l["preco"], "granel": False, "un": "",
                      "chave": f"solto:{l['id']}", "situacao": l["status"] or ""})
    return saida


@tipo_lista("estoque_estranho", "Estoque suspeito no canal",
            "Estoque redondo demais (1000, 10000) costuma ser valor provisório, não o real.")
def _estoque_estranho(con, cid):
    suspeitos = {100.0, 500.0, 999.0, 1000.0, 9999.0, 10000.0, 99999.0}
    saida = []
    for canal in MARCA["canais"]:
        for pid, l in _mapa_canal(con, cid, canal).items():
            if l["estoque"] is None or l["estoque"] not in suspeitos:
                continue
            p = con.execute("SELECT * FROM produtos WHERE id=?", (pid,)).fetchone()
            if not p:
                continue
            saida.append(_item(p, {"chave": f"est:{canal}:{pid}",
                                   "canal_nome": CANAIS[canal]["nome"],
                                   "situacao": f"estoque {int(l['estoque'])} no {CANAIS[canal]['nome']}"}))
    return saida


def lista(con, cid, tipo, q="", mostrar_feitos=False):
    if tipo not in TIPOS:
        raise ValueError("Lista desconhecida.")
    itens = TIPOS[tipo]["fn"](con, cid)
    feitos = {r["chave"] for r in
              con.execute("SELECT chave FROM tarefas WHERE cliente_id=? AND tipo=? AND feito_em IS NOT NULL",
                          (cid, tipo))}
    for i in itens:
        i["feito"] = i.get("chave") in feitos
    if not mostrar_feitos:
        itens = [i for i in itens if not i["feito"]]
    if q:
        alvo = norm_nome(q)
        digitos = "".join(ch for ch in q if ch.isdigit())
        itens = [i for i in itens
                 if alvo in norm_nome(i.get("nome")) or alvo in norm_nome(i.get("marca"))
                 or (digitos and digitos in str(i.get("ean") or ""))
                 or (q.strip().lower() in str(i.get("codigo") or "").lower())]
    return itens, len(feitos)


def resumo(con, cid):
    cartoes = []
    for chave, t in TIPOS.items():
        if t["canal"] and t["canal"] not in MARCA["canais"]:
            continue
        itens, feitos = lista(con, cid, chave)
        explica = t["explica"]
        if t["canal"] and canal_vazio(con, cid, t["canal"]):
            c = CANAIS[t["canal"]]
            explica = (f"A planilha {c['artigo']} {c['nome']} ainda não foi importada, "
                       "então não há o que comparar.")
        cartoes.append({"chave": chave, "titulo": t["titulo"], "explica": explica,
                        "quantos": len(itens), "feitos": feitos})
    prod = con.execute("SELECT COUNT(*) n FROM produtos WHERE cliente_id=?", (cid,)).fetchone()["n"]
    certos = 0
    if prod:
        com_problema = set()
        for chave in TIPOS:
            if TIPOS[chave]["canal"] and TIPOS[chave]["canal"] not in MARCA["canais"]:
                continue
            for i in lista(con, cid, chave)[0]:
                if i.get("produto_id"):
                    com_problema.add(i["produto_id"])
        certos = prod - len(com_problema)
    canais = []
    meta = pegar_meta(con, cid)
    for canal in MARCA["canais"]:
        n = con.execute("SELECT COUNT(*) n FROM canais WHERE cliente_id=? AND canal=?",
                        (cid, canal)).fetchone()["n"]
        canais.append({"canal": canal, "nome": CANAIS[canal]["nome"], "linhas": n,
                       "importado": meta.get(f"importado_{canal}", "")})
    return {"produtos": prod, "certos": certos, "cartoes": cartoes, "canais": canais,
            "importado_loja": meta.get("importado_loja", ""),
            "importado_fotos": meta.get("importado_fotos", "")}


def detalhe(con, cid, pid):
    p = con.execute("SELECT * FROM produtos WHERE id=? AND cliente_id=?", (pid, cid)).fetchone()
    if not p:
        raise ValueError("Produto não encontrado.")
    d = _item(p, {"estoque": p["estoque"], "tem_foto": bool(p["tem_foto"]), "obs": p["obs"] or ""})
    d["canais"] = []
    for canal in MARCA["canais"]:
        l = _mapa_canal(con, cid, canal).get(pid)
        esperado = preco_esperado(canal, p["preco_loja"])
        d["canais"].append({
            "canal": canal, "nome": CANAIS[canal]["nome"],
            "presente": l is not None,
            "nome_canal": (l["nome"] if l else "") or "",
            "codigo": (l["codigo"] if l else "") or "",
            "preco": l["preco"] if l else None,
            "esperado": esperado,
            "status": (l["status"] if l else "") or "",
            "vendendo": vendendo(l["status"]) if l else False,
            "categoria": (l["categoria"] if l else "") or "",
            "estoque": l["estoque"] if l else None,
            "tem_foto": bool(l["tem_foto"]) if l else False,
        })
    d["historico"] = [{"texto": r["texto"], "quando": r["quando"]} for r in con.execute(
        "SELECT texto,quando FROM historico WHERE produto_id=? ORDER BY id DESC LIMIT 30", (pid,))]
    return d


# -------------------------------------------------------------- CSV de saída

def csv_lista(con, cid, tipo, q=""):
    itens, _ = lista(con, cid, tipo, q, mostrar_feitos=True)
    cols = ["nome", "ean", "codigo", "marca", "categoria", "un", "preco_loja",
            "preco_canal", "esperado", "diferenca", "situacao", "nome_canal", "feito"]
    saida = io.StringIO()
    w = csv.writer(saida, delimiter=";")
    w.writerow(["Nome", "EAN", "Código", "Marca", "Categoria", "Un", "Preço loja",
                "Preço no canal", "Preço esperado", "Diferença", "Situação", "Nome no canal", "Feito"])
    for i in itens:
        w.writerow([i.get(c) if c not in {"preco_loja", "preco_canal", "esperado", "diferenca"}
                    else reais(i.get(c)) for c in cols[:-1]] + ["sim" if i.get("feito") else ""])
    return saida.getvalue().encode("utf-8-sig")


MODELOS = {
    "loja": ("modelo-lista-da-loja.csv",
             [["Codigo de barras", "Nome do produto", "Preco", "Marca", "Categoria", "Unidade", "Estoque"],
              ["7891000100103", "Racao Exemplo Adulto 10kg", "89,90", "Exemplo", "Cães", "UN", "7"],
              ["", "Racao Granel Exemplo", "12,50", "Exemplo", "Cães", "KG", "25"]]),
    "canal": ("modelo-exportacao-do-canal.csv",
              [["EAN do produto", "Codigo", "Nome do produto", "Preco", "Situacao", "Categoria", "Estoque", "Foto"],
               ["7891000100103", "", "Racao Exemplo Adulto 10kg", "128,43", "Em venda", "Cães", "7", "sim"],
               ["", "SM12345", "Racao Granel Exemplo", "17,86", "Em analise", "Cães", "1000", "nao"]]),
    "fotos": ("modelo-lista-de-fotos.csv",
              [["Nome do arquivo"], ["7891000100103.jpg"], ["7891000100110.png"]]),
}


def csv_modelo(grupo):
    nome, linhas = MODELOS[grupo]
    saida = io.StringIO()
    w = csv.writer(saida, delimiter=";")
    for l in linhas:
        w.writerow(l)
    return nome, saida.getvalue().encode("utf-8-sig")


# -------------------------------------------------------------------- servidor

def _json_bytes(obj):
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


class Painel(BaseHTTPRequestHandler):
    server_version = "PainelCatalogos"
    protocol_version = "HTTP/1.1"

    def log_message(self, formato, *args):
        if "--quieto" not in sys.argv:
            sys.stderr.write("%s - %s\n" % (self.address_string(), formato % args))

    # ---- respostas

    def _responder(self, corpo, tipo="application/json; charset=utf-8", status=200, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(corpo)

    def _ok(self, obj):
        self._responder(_json_bytes(obj))

    def _erro(self, msg, status=400):
        self._responder(_json_bytes({"erro": str(msg)}), status=status)

    def _pagina(self, nome):
        caminho = os.path.join(STATIC, nome)
        if not os.path.abspath(caminho).startswith(os.path.abspath(STATIC)) \
                or not os.path.isfile(caminho):
            return self._erro("Página não encontrada.", 404)
        with open(caminho, "rb") as f:
            corpo = f.read()
        tipos = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
                 ".ico": "image/x-icon"}
        ext = os.path.splitext(nome)[1].lower()
        self._responder(corpo, tipos.get(ext, "application/octet-stream"))

    def _corpo(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > LIMITE_UPLOAD:
            raise ValueError(f"Arquivo grande demais (o limite é {LIMITE_UPLOAD // 1_000_000} MB).")
        if not n:
            return b""
        dados = b""
        while len(dados) < n:
            pedaco = self.rfile.read(min(65536, n - len(dados)))
            if not pedaco:
                break
            dados += pedaco
        return dados

    def _origem_ok(self):
        """Recusa chamadas disparadas por outra página."""
        origem = self.headers.get("Origin")
        if not origem:
            return True
        return urlparse(origem).netloc == (self.headers.get("Host") or "")

    # ---- rotas

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        rota = u.path.rstrip("/") or "/"
        try:
            if rota == "/":
                return self._pagina("index.html")
            if rota == "/importar":
                return self._pagina("importar.html")
            if rota == "/de-para":
                return self._pagina("depara.html")
            if rota.startswith("/static/"):
                return self._pagina(rota[len("/static/"):])
            if rota == "/favicon.ico":
                return self._responder(b"", "image/x-icon")
            if rota.startswith("/api/"):
                return self._api_get(rota[len("/api/"):], q)
            return self._erro("Endereço não encontrado.", 404)
        except ValueError as e:
            return self._erro(e)
        except Exception as e:                                    # nunca 500 cru para a usuária
            self.log_message("erro em %s: %r", rota, e)
            return self._erro(f"Deu um problema aqui dentro: {e}", 500)

    def _api_get(self, nome, q):
        con = conectar()
        try:
            cli = cliente_atual(con, q.get("cliente"))
            cid = cli["id"]
            if nome == "marca":
                return self._ok({
                    "nome": MARCA.get("nome"), "cor": MARCA.get("cor"),
                    "cor_escura": MARCA.get("cor_escura"),
                    "divisor": MARCA.get("divisor_entrega"),
                    "arredondamento": MARCA.get("arredondamento"),
                    "tolerancia": MARCA.get("tolerancia_preco"),
                    "canais": [{"canal": c, "nome": CANAIS[c]["nome"],
                                "entrega": CANAIS[c]["entrega"]} for c in MARCA["canais"]],
                    "campos": {g: [{"campo": c, "rotulo": r, "obrigatorio": o}
                                   for c, r, o in CAMPOS[g]] for g in CAMPOS},
                    "limite_tela": MAX_LINHAS_TELA,
                })
            if nome == "clientes":
                return self._ok({
                    "atual": cid,
                    "clientes": [{"id": r["id"], "nome": r["nome"]} for r in
                                 con.execute("SELECT id,nome FROM clientes ORDER BY nome")]})
            if nome == "resumo":
                r = resumo(con, cid)
                r["cliente"] = {"id": cid, "nome": cli["nome"]}
                return self._ok(r)
            if nome == "lista":
                tipo = q.get("tipo") or ""
                itens, feitos = lista(con, cid, tipo, q.get("q", ""),
                                      q.get("feitos") == "1")
                total = len(itens)
                return self._ok({"tipo": tipo, "titulo": TIPOS[tipo]["titulo"],
                                 "explica": TIPOS[tipo]["explica"], "total": total,
                                 "feitos": feitos, "limite": MAX_LINHAS_TELA,
                                 "cortado": total > MAX_LINHAS_TELA,
                                 "itens": itens[:MAX_LINHAS_TELA]})
            if nome == "produto":
                return self._ok(detalhe(con, cid, int(q.get("id") or 0)))
            if nome == "produtos":
                busca = norm_nome(q.get("q", ""))
                saida = []
                for p in _produtos(con, cid):
                    if busca and busca not in norm_nome(p["nome"]) \
                            and busca not in (p["ean_norm"] or ""):
                        continue
                    saida.append({"id": p["id"], "nome": p["nome"], "ean": p["ean"] or "",
                                  "preco_loja": p["preco_loja"]})
                    if len(saida) >= 60:
                        break
                return self._ok({"itens": saida})
            if nome == "importacoes":
                return self._ok({"itens": [dict(r) for r in con.execute(
                    "SELECT tipo,arquivo,linhas,novos,ligados,soltos,quando FROM importacoes "
                    "WHERE cliente_id=? ORDER BY id DESC LIMIT 20", (cid,))]})
            if nome == "csv":
                tipo = q.get("tipo") or ""
                if tipo not in TIPOS:
                    raise ValueError("Lista desconhecida.")
                corpo = csv_lista(con, cid, tipo, q.get("q", ""))
                arq = f"{tipo}-{hoje()}.csv"
                return self._responder(corpo, "text/csv; charset=utf-8", extra={
                    "Content-Disposition": f'attachment; filename="{arq}"'})
            if nome == "modelo":
                grupo = q.get("grupo") or "loja"
                if grupo not in MODELOS:
                    raise ValueError("Modelo desconhecido.")
                arq, corpo = csv_modelo(grupo)
                return self._responder(corpo, "text/csv; charset=utf-8", extra={
                    "Content-Disposition": f'attachment; filename="{arq}"'})
            return self._erro("Endereço não encontrado.", 404)
        finally:
            con.close()

    def do_POST(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        rota = u.path.rstrip("/") or "/"
        if not self._origem_ok():
            return self._erro("Chamada recusada: veio de outra página.", 403)
        try:
            corpo = self._corpo()
        except ValueError as e:
            return self._erro(e)
        con = conectar()
        try:
            cli = cliente_atual(con, q.get("cliente"))
            cid = cli["id"]
            if rota == "/api/analisar":
                cab, linhas = ler_tabela(corpo)
                grupo = q.get("grupo") or "canal"
                if grupo not in CAMPOS:
                    raise ValueError("Tipo de planilha desconhecido.")
                return self._ok({
                    "colunas": cab, "linhas": len(linhas),
                    "mapa": sugerir_mapa(cab, grupo),
                    "amostra": [[("" if c is None else str(c))[:60] for c in l] for l in linhas[:5]]})
            if rota == "/api/importar":
                tipo = q.get("tipo") or ""
                if tipo not in ({"loja", "fotos"} | set(MARCA["canais"])):
                    raise ValueError("Tipo de importação desconhecido.")
                try:
                    mapa = {k: int(v) for k, v in json.loads(q.get("mapa") or "{}").items()}
                except (ValueError, AttributeError):
                    raise ValueError("Não entendi o mapeamento das colunas.")
                r = importar(con, cid, tipo, corpo, mapa, q.get("arquivo", ""))
                return self._ok(r)
            dados = json.loads(corpo or b"{}")
            if rota == "/api/cliente":
                nome = (dados.get("nome") or "").strip()
                if len(nome) < 2:
                    raise ValueError("Escreva o nome do cliente.")
                if con.execute("SELECT 1 FROM clientes WHERE nome=?", (nome,)).fetchone():
                    raise ValueError("Já existe um cliente com esse nome.")
                cur = con.execute("INSERT INTO clientes(nome,cor,criado_em) VALUES(?,?,?)",
                                  (nome, MARCA.get("cor"), agora()))
                con.commit()
                return self._ok({"id": cur.lastrowid, "nome": nome})
            if rota == "/api/tarefa":
                tipo, chave = dados.get("tipo") or "", dados.get("chave") or ""
                if tipo not in TIPOS or not chave:
                    raise ValueError("Não entendi qual item marcar.")
                if dados.get("feito"):
                    con.execute("INSERT INTO tarefas(cliente_id,tipo,chave,feito_em) VALUES(?,?,?,?) "
                                "ON CONFLICT(cliente_id,tipo,chave) DO UPDATE SET feito_em=excluded.feito_em",
                                (cid, tipo, chave, agora()))
                else:
                    con.execute("DELETE FROM tarefas WHERE cliente_id=? AND tipo=? AND chave=?",
                                (cid, tipo, chave))
                con.commit()
                return self._ok({"ok": True})
            if rota == "/api/ligar":
                linha_id, pid = dados.get("canal_linha_id"), dados.get("produto_id")
                l = con.execute("SELECT * FROM canais WHERE id=? AND cliente_id=?",
                                (linha_id, cid)).fetchone()
                if not l:
                    raise ValueError("Linha de canal não encontrada.")
                p = con.execute("SELECT * FROM produtos WHERE id=? AND cliente_id=?",
                                (pid, cid)).fetchone()
                if not p:
                    raise ValueError("Produto não encontrado.")
                chave = norm_codigo(l["codigo"]) or norm_ean(l["ean"]) or norm_nome(l["nome"])
                if not chave:
                    raise ValueError("Essa linha do canal não tem código nem nome para guardar a ligação.")
                con.execute("INSERT INTO de_para(cliente_id,canal,chave,produto_id,quando) "
                            "VALUES(?,?,?,?,?) ON CONFLICT(cliente_id,canal,chave) "
                            "DO UPDATE SET produto_id=excluded.produto_id, quando=excluded.quando",
                            (cid, l["canal"], chave, pid, agora()))
                con.execute("UPDATE canais SET produto_id=? WHERE id=?", (pid, linha_id))
                anotar(con, cid, pid, f"Ligado à linha \"{l['nome']}\" do {CANAIS[l['canal']]['nome']}")
                con.commit()
                return self._ok({"ok": True, "nome": p["nome"]})
            if rota == "/api/produto":
                pid = dados.get("id")
                p = con.execute("SELECT * FROM produtos WHERE id=? AND cliente_id=?",
                                (pid, cid)).fetchone()
                if not p:
                    raise ValueError("Produto não encontrado.")
                mudou = []
                if "preco_loja" in dados:
                    v = dados["preco_loja"]
                    novo = None if v in (None, "") else num(v)
                    if v not in (None, "") and novo is None:
                        raise ValueError("O preço precisa ser um número, como 89,90.")
                    if novo is not None and novo < 0:
                        raise ValueError("O preço não pode ser negativo.")
                    con.execute("UPDATE produtos SET preco_loja=?, atualizado_em=? WHERE id=?",
                                (novo, agora(), pid))
                    mudou.append(f"Preço da loja: {reais(p['preco_loja'])} -> {reais(novo)}")
                if "ean" in dados:
                    bruto = (dados["ean"] or "").strip()
                    e = norm_ean(bruto)
                    if bruto and not e:
                        raise ValueError("O código de barras precisa ter pelo menos 8 números.")
                    con.execute("UPDATE produtos SET ean=?, ean_norm=?, atualizado_em=? WHERE id=?",
                                (bruto or None, e, agora(), pid))
                    mudou.append(f"Código de barras: {p['ean'] or '(vazio)'} -> {bruto or '(vazio)'}")
                    religar_tudo(con, cid)
                if "nome" in dados:
                    nome = (dados["nome"] or "").strip()
                    if len(nome) < 2:
                        raise ValueError("O nome precisa ter pelo menos 2 letras.")
                    con.execute("UPDATE produtos SET nome=?, nome_norm=?, atualizado_em=? WHERE id=?",
                                (nome, norm_nome(nome), agora(), pid))
                    mudou.append(f"Nome: {p['nome']} -> {nome}")
                if "obs" in dados:
                    con.execute("UPDATE produtos SET obs=?, atualizado_em=? WHERE id=?",
                                ((dados["obs"] or "").strip()[:500], agora(), pid))
                    mudou.append("Anotação alterada")
                for t in mudou:
                    anotar(con, cid, pid, t)
                con.commit()
                return self._ok({"ok": True, "mudou": mudou})
            if rota == "/api/apagar-canal":
                canal = dados.get("canal")
                if canal not in MARCA["canais"]:
                    raise ValueError("Canal desconhecido.")
                n = con.execute("DELETE FROM canais WHERE cliente_id=? AND canal=?",
                                (cid, canal)).rowcount
                con.execute("DELETE FROM meta WHERE cliente_id=? AND chave=?",
                            (cid, f"importado_{canal}"))
                con.commit()
                return self._ok({"ok": True, "apagadas": n})
            return self._erro("Endereço não encontrado.", 404)
        except ValueError as e:
            return self._erro(e)
        except json.JSONDecodeError:
            return self._erro("Não entendi os dados enviados.")
        except Exception as e:
            self.log_message("erro em %s: %r", rota, e)
            return self._erro(f"Deu um problema aqui dentro: {e}", 500)
        finally:
            con.close()


def servir(abrir=True):
    host = os.environ.get("PAINEL_HOST") or "127.0.0.1"
    porta = int(MARCA.get("porta") or 8767)
    conectar().close()
    servidor = ThreadingHTTPServer((host, porta), Painel)
    endereco = f"http://{host}:{porta}"
    print(f"{MARCA.get('nome')} em {endereco}")
    print("Deixe esta janela aberta enquanto usa o painel. Para fechar: Ctrl+C.")
    if abrir:
        threading.Timer(1.0, lambda: webbrowser.open(endereco)).start()
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nPainel fechado.")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "servir":
        servir(abrir="--sem-navegador" not in sys.argv)
    else:
        servir(abrir=True)
