# Caixa Humanize

Código-fonte do painel financeiro da Humanize, da Moara e do pessoal.

## O que ele faz

Página única, feita para o celular, que controla:

- Três centros financeiros: Humanize, Moara e Pessoal, sem misturar saldo.
- Teto de retirada por sócia, travado pelo menor valor entre caixa livre e lucro
  ainda não distribuído.
- Clientes, contas a pagar e contas recorrentes.
- Plano de Reorganização Financeira, com dívidas e planejamento mês a mês.
- Orçamento por categoria com alertas, projeção de seis meses e histórico de
  alterações.
- Saldo por conta, para conferir contra o extrato de cada banco.

## Como os lançamentos contam

| tipo | efeito |
| --- | --- |
| Normal | mexe no caixa e no lucro do centro, quando o status é pago |
| Rateio casa e empresa | um lançamento só, com o valor total, dividido entre empresa e pessoal; o consolidado soma uma vez |
| Retirada de sócia | sai do caixa da empresa, entra no pessoal; não é despesa, não derruba o lucro |
| Adiantamento de retirada | a empresa pagou algo pessoal; sai do caixa, não mexe no lucro e abate do teto de quem usou |
| Empréstimo e aporte de sócia | entram no caixa, não são receita e não liberam retirada |

## Rodando

O arquivo não funciona sozinho. Os dados ficam no banco do Artifact da
claude.ai, e a página só alcança esse banco quando aberta pelo link do painel.
Aberto direto do repositório, ele renderiza a interface vazia e avisa isso.

Nenhum dado financeiro está neste arquivo.
