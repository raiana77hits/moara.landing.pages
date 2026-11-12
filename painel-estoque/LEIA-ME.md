# Painel de Estoque · Casa de Embalagens Cangaíba

Cadastro central dos produtos, com a situação de cada um na Nuvemshop, no iFood e na 99Food.
Roda só neste computador. **Não altera nada nas lojas online nem no Gestor Z.**

## Como abrir (Windows)

1. Instale o Python uma única vez: https://www.python.org/downloads/ (marque "Add python.exe to PATH").
2. Dê dois cliques em `iniciar.bat`.
3. O painel abre no navegador em http://127.0.0.1:8765. Deixe a janela preta aberta enquanto usa.

## O que dá para fazer

- Buscar por nome, código de barras, SKU ou código do Gestor Z.
- Digitar o **estoque oficial** de cada produto (Enter pula para o próximo). Fica registrado com data e histórico.
- Filtrar: falta conferir, sem estoque mas à venda, fora de alguma loja, etc.
- Baixar a lista filtrada em CSV (abre no Excel).
- Atualizar os dados: ler a Nuvemshop de novo ou importar um CSV novo.

## Acesso à Nuvemshop

Copie `config.exemplo.json` para `config.json` e cole o token. O arquivo fica só neste computador.
Sem ele o painel funciona normalmente, só não relê a Nuvemshop.

## Onde ficam os dados

Tudo em `banco.db`, nesta pasta. Para fazer backup, copie esse arquivo.
