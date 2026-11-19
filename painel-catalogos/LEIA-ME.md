# Painel de Catálogos

Compara a **lista de produtos da loja** com o que está no **iFood**, no **Anota Aí** e na
**99Food**, e mostra o que está diferente: o que falta cadastrar, qual preço está errado,
o que está sem foto e o que está parado.

**Este painel não escreve em loja nenhuma.** Ele só lê as planilhas que você manda.
Quem corrige no iFood e no Anota Aí é você, pelo portal deles. O painel só diz o que corrigir.

Serve para mais de um cliente: o seletor no alto da tela troca entre eles, e cada um tem
os seus próprios produtos e canais.

## Como abrir (Windows)

1. Instale o Python uma vez só: https://www.python.org/downloads/
   (marque **"Add python.exe to PATH"** na primeira tela).
2. Dê dois cliques em `iniciar.bat`.
3. O painel abre no navegador em http://127.0.0.1:8767.
   Deixe a janela preta aberta enquanto usa. Para fechar o painel, feche a janela preta.

Se você também usa o Painel de Estoque da Casa de Embalagens, os dois podem ficar abertos
ao mesmo tempo: um usa a porta 8765 e este usa a 8767.

## O caminho das pedras, na primeira vez

Na aba **Atualizar dados**, mande as planilhas nesta ordem:

| Ordem | O que mandar | De onde sai |
|---|---|---|
| 1 | **Lista de produtos da loja** | a planilha de preços/estoque da loja |
| 2 | **Exportação do iFood** | Portal do Parceiro, Catálogo, exportar |
| 3 | **Exportação do Anota Aí** | o catálogo exportado do Anota Aí |
| 4 | **Lista de fotos do Drive** | os nomes dos arquivos da pasta de fotos |

A lista da loja vem primeiro porque é ela que manda: o preço dela é o preço que vale.

Em cada envio o painel mostra **quais colunas ele achou** e você confere antes de gravar.
Se ele errar alguma, troque na listinha. Nada é gravado antes de você clicar em
**Gravar no painel**.

Aceita **CSV** e **XLSX** (Excel), até 20 MB.

### Como tirar a lista de nomes das fotos do Drive

Na pasta de fotos, no computador: abra a pasta, clique na barra de endereço, escreva `cmd`
e dê Enter. Na janela preta, escreva isto e dê Enter:

```bat
dir /b > lista-de-fotos.csv
```

Vai aparecer um arquivo `lista-de-fotos.csv` na pasta. É esse que você manda no painel.
O nome de cada arquivo precisa ser o código de barras do produto (ex.: `7891000100103.jpg`),
que é como a pasta da Vibe Pet já está.

## O que cada lista quer dizer

- **Falta cadastrar no iFood / no Anota Aí** — o produto está na lista da loja mas não
  aparece no canal, ou está lá sem vender (recusado, em análise, pausado).
- **Preço diferente no iFood** — o preço do iFood deveria ser o da loja **dividido por 0,70**.
  O painel mostra quanto está, quanto deveria estar e a diferença em reais.
- **Preço diferente no Anota Aí** — no Anota Aí o preço deveria ser **igual** ao da loja.
- **Sem foto** — sem foto na pasta do Drive e/ou sem foto no canal.
- **Sem código de barras** — sem EAN o painel não consegue casar o produto entre loja e canais.
  É o caso normal da ração a granel.
- **Repetido na lista da loja** — o mesmo código de barras ou o mesmo nome em duas linhas.
  É assim que aparece o caso do Premier com dois EANs.
- **Parado no iFood** — tudo que está em análise, recusado ou esperando cadastro.
- **Linhas de canal sem produto** — ver a aba **De-para**, abaixo.
- **Estoque suspeito no canal** — estoque 1000, 10000 e parecidos, que costumam ser valor
  provisório, não o real.

Cada item tem uma **caixinha para marcar como resolvido**. O que você marca some da lista
e não volta a incomodar. Para rever o que já marcou, ligue **"Mostrar os já resolvidos"**.

Toda lista tem **Baixar em CSV**, que abre no Excel, com tudo (inclusive o que já está
resolvido), sem o limite de tela.

## De-para: a ração a granel

O iFood não aceita nome livre: a ração a granel entra pelos códigos **SM…** da biblioteca
dele, sem código de barras real. Quando o painel não consegue casar uma linha do canal com
um produto da loja, ela vai para a aba **De-para**.

Lá você busca o produto certo e clica. O painel **guarda a ligação** e não pergunta mais,
nem nas próximas importações.

O painel também não chuta: se duas linhas da loja têm o mesmo nome, ele manda para o De-para
em vez de escolher uma por conta própria.

## A ficha do produto

Clique num item de qualquer lista. Abre a ficha com:

- o produto em **cada canal**, lado a lado: se está lá, por quanto, quanto deveria ser,
  a situação e se tem foto;
- campos para corrigir **nome, preço da loja, código de barras e uma anotação sua**;
- o **histórico** de tudo que mudou, com data.

O que você corrige aqui vale só dentro do painel — é para arrumar erro da planilha. A loja
e os canais não mudam.

## Mudar as regras

O arquivo `marca.json`, nesta pasta, guarda as configurações. Abra no Bloco de Notas:

```json
{
  "nome": "Painel de Catálogos",
  "porta": 8767,
  "cor": "#1e3fae",
  "divisor_entrega": 0.70,
  "arredondamento": 0.01,
  "tolerancia_preco": 0.05,
  "canais": ["ifood", "anota", "n99"],
  "cliente_inicial": "Vibe Pet"
}
```

- **divisor_entrega** — a regra dos apps de entrega. 0,70 quer dizer "preço da loja ÷ 0,70".
- **arredondamento** — para quanto arredondar o preço esperado, para cima.
  `0.01` = centavo, `0.10` = dez centavos, `1` = real inteiro.
- **tolerancia_preco** — diferença em reais que o painel deixa passar sem reclamar.
  Com 0,05, um preço 3 centavos acima do esperado não vira pendência.
- **canais** — tire da lista o canal que o cliente não usa e ele some da tela.
- **cor** e **nome** — para o painel ficar com a cara do cliente.

Depois de salvar, feche a janela preta e abra o `iniciar.bat` de novo.

## Onde ficam os dados

Tudo em `banco.db`, nesta pasta. Para fazer backup, **copie esse arquivo**. Para restaurar,
feche o painel e ponha o arquivo de volta no lugar.

Nenhuma chave, token ou senha fica aqui: o painel não se conecta a nenhuma loja, então não
precisa de nenhuma.

## Abrir sozinho quando o computador liga

1. Clique com o botão direito em `iniciar-com-windows.bat`, **Criar atalho**.
2. Aperte **Windows + R**, escreva `shell:startup` e Enter.
3. Arraste o atalho para dentro dessa pasta.
4. Botão direito no atalho, **Propriedades**, em **Executar** escolha **Minimizada**, OK.

## Se der problema

- **"python não é reconhecido"** — o Python não foi instalado com "Add python.exe to PATH".
  Instale de novo marcando essa caixinha.
- **O navegador não abre** — escreva http://127.0.0.1:8767 na barra de endereço.
- **"Não consegui falar com o painel"** — a janela preta fechou. Abra o `iniciar.bat`.
- **A tela está velha depois de uma atualização** — aperte **Ctrl+F5**.
- **"Isso não parece uma planilha .xlsx"** — salve de novo como CSV ou XLSX e tente.
- **Uma coluna foi lida errada** — no passo 3 da importação, troque na listinha antes de gravar.

## Para quem for mexer no código

- Python 3 só com a biblioteca padrão (`http.server`, `sqlite3`, `zipfile`, `csv`).
  Nenhuma dependência para instalar.
- Um servidor só, em `painel.py`. Telas em `static/` (HTML e JavaScript puro).
- Variáveis de ambiente para teste: `PAINEL_DB`, `PAINEL_MARCA`, `PAINEL_PORTA`, `PAINEL_HOST`.
- Para acrescentar uma lista nova, use o decorador `@tipo_lista(...)` em `painel.py`:
  ela aparece sozinha na tela inicial, com busca, CSV e o "marcar como resolvido".
- O leitor de XLSX aguenta `inlineStr` e referências de coluna fora do padrão, que alguns
  sistemas geram.
