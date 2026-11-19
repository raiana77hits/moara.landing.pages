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

## Conferência de prateleira pelo celular

A tela `/conferir` é feita para o celular: a câmera lê o código de barras, o produto aparece e
você digita a quantidade. Também aceita leitor Bluetooth e busca por nome. Código que não está
no cadastro pode ser anotado para cadastrar depois.

O celular só libera a câmera em endereço com cadeado (https). Para isso o painel fica no seu
computador e o celular chega nele por um "túnel" privado do Tailscale (gratuito para uso pessoal;
confira o limite atual de aparelhos em tailscale.com/pricing). Só os seus aparelhos entram na
sua conta Tailscale, e o painel ainda pede a senha.

### Configurar (uma vez só)

1. **Senha:** dê dois cliques em `definir-senha.bat` e crie uma senha de pelo menos 8 caracteres.
2. **Computador:** instale o Tailscale em https://tailscale.com/download e entre com uma conta
   (Google, por exemplo). No painel de administração (login.tailscale.com → DNS), confira que
   **MagicDNS** e **HTTPS Certificates** estão ligados.
3. **Celular:** instale o app Tailscale (App Store ou Play Store) e entre **com a mesma conta**.
4. **Túnel:** abra o painel (`iniciar.bat`) e depois dê dois cliques em `conectar-celular.bat`.
   Ele mostra o endereço do painel, algo como `https://seu-computador.xxxx.ts.net`.
5. **No celular**, com o Tailscale ligado, abra esse endereço e some `/conferir` no final.
   Entre com a senha. Depois use "Adicionar à tela inicial" do navegador para virar um ícone.

O túnel fica guardado: depois de feito, `conectar-celular.bat` não precisa ser repetido, a não
ser que você desligue. Para desligar: `tailscale serve --https=443 off` no terminal.

### Antes de ir para a loja

- Teste em casa, com o celular no 4G (Wi-Fi desligado): se abre e a câmera lê, vai funcionar lá.
- Deixe o computador **ligado e conectado à internet**, com o painel (`iniciar.bat`) aberto. O
  painel pede ao Windows para não suspender enquanto está aberto, mas desligue também a suspensão
  nas configurações de energia. A janela do painel não pode ser fechada.
- Se o computador reiniciar, abra `iniciar.bat` de novo.
- No Windows, o Tailscale pode desconectar quando você sai da sua conta de usuário; não saia da
  conta (bloquear a tela está certo).

### Se der erro

- **"Câmera só com https":** você abriu o endereço sem o cadeado. Use o que começa com https.
- **Não abre no celular:** confira se o Tailscale está ligado no celular e no computador.
- **Pede senha de novo:** normal depois de 30 dias ou se trocar a senha.

## Fotos dos produtos

A foto do produto aparece ao lado dele (a que já está no site da Nuvemshop). Quando o produto
não tem foto, a tela de conferência mostra um botão amarelo grande: toque nele, tire a foto na
prateleira e ela fica guardada no painel (pasta `fotos`, ao lado do `banco.db`).

- O celular não deixa um site abrir a câmera sozinho; é preciso tocar no botão.
- As fotos tiradas ficam **só no painel**. Nada é enviado para a Nuvemshop, iFood ou 99Food.
- Para pegar todas as fotos tiradas: tela inicial, "Baixar fotos tiradas". Vem um ZIP com nomes
  no formato `SKU__nome-do-produto__01.jpg`.
- O filtro "Sem foto" mostra tudo o que ainda precisa ser fotografado.

## Enviar o estoque contado para a Nuvemshop

Botão **Enviar para as lojas** (no celular: Início, Ferramentas).

1. **Conectar:** na primeira vez, cole o token de acesso do aplicativo e o número da loja. Ficam só
   neste computador, no `config.json`.
2. **Ver o que vai mudar:** o painel mostra a lista antes de gravar. Nada é enviado nesta etapa.
   - *Só zerar o que acabou* (padrão): produtos contados com 0 passam a aparecer sem estoque no site.
   - *Zerar e repor*: também grava a quantidade contada dos outros. Atenção: o site passa a
     controlar o estoque desses produtos, mas as vendas do caixa, do iFood e da 99Food não baixam o
     estoque do site.
3. **Gravar:** pede confirmação, guarda um backup na pasta `backups` e confere na loja depois.
   Vão no máximo 300 por vez.
4. **Desfazer:** a mesma tela lista as gravações feitas e devolve o estoque ao valor anterior.
   Se o valor mudou na loja depois da gravação, aquele item é pulado.

## Prioridades: o que contar e comprar

Tela `/prioridades` (Início, Ferramentas, "Prioridades"). Usa só os dados que o painel já tem
(vendas importadas, notas, contagens), sem precisar contar a prateleira toda:

- **Curva ABC** pelo valor vendido: A = 80% do faturamento, B = até 95%, C = o resto.
- **Semáforo:** vermelho = provável falta (estoque estimado zerado, cobertura menor que 7 dias, ou
  produto A/B que vende bem, nunca foi contado e não tem compra nos últimos 60 dias);
  amarelo = conferir ou comprar; verde = sem alerta; cinza = sem venda no período (candidato a
  ocultar, nunca apagar).
- **Comprar (30 d):** venda por dia x 30 menos o estoque estimado. É sugestão, não pedido.
- A etiqueta **contar** marca os A/B nunca contados: conte primeiro esses.
- Vendas e notas podem ser importadas em CSV ou Excel (.xlsx). Informe as datas certas do relatório.
- Estoque estimado é alerta, não contagem.

## iFood (API oficial)

Início, Ferramentas, **iFood (API): conectar e testar**. O painel fala com o módulo **Item** da API do iFood
(mercado/groceries), no mesmo estilo das outras telas: **prévia antes de enviar, confirmação e backup**.

### Conectar
- Escolha **Loja de teste** ou **Loja real**, cole o **Client ID** e o **Client Secret** do aplicativo e clique em
  **Salvar e testar**. As chaves ficam só neste computador (`config.json`); a tela nunca as mostra de volta.
- Use o aplicativo **centralizado** ("Teste (C)" para testes). Se o ID e o segredo estiverem trocados, o painel acerta sozinho.
- A loja real responde "sem permissão liberada" até a homologação ser aprovada: é esperado.

### Enviar produtos (só para a loja de teste)
Na mesma tela, busque e marque produtos e escolha o tipo de envio. **Ver o que seria enviado** mostra o corpo exato.
| Tipo | Chamada | Para quê |
|---|---|---|
| Criar ou atualizar tudo | `POST /item/v1.0/ingestion/{loja}?reset=false` | produto novo ou reenviar todos os dados |
| Só preço, promoção e estoque | `PATCH /item/v1.0/ingestion/{loja}` | produto que já foi enviado (o PATCH não ativa produto) |
| Carga completa | `POST ...?reset=true` | substitui o catálogo da loja de teste; pede digitar RESET |

Regras do painel:
- Campos enviados: `barcode` e `name` (obrigatórios), `plu` (SKU do Gestor Z), `active`, `prices.price`,
  `prices.promotionPrice` (precisa ser menor que o preço), `details` (foto, descrição, unidade, categoria) e `inventory.stock`.
- **Preço no iFood = preço do caixa dividido por 0,70** (mesma regra da planilha).
- **Estoque:** usa o estimado; se o estimado zerou mas a contagem é maior que zero, envia a **contagem** e avisa na prévia.
- **Sem preço ou sem código de barras:** o envio é bloqueado. Código que começa com 2 (interno do caixa) é avisado.
- Produto pesável: o iFood aceita EAN fictício ou código interno no campo `barcode`.
- Se o iFood responder 429 (muitas requisições), o painel espera e tenta de novo.
- A resposta de cada envio fica em `backups/ifood_api_teste_<tipo>_<data>.json` (corpo enviado, código e `integrationUuid`).
- **A loja real está bloqueada no código** até a homologação ser aprovada.

### O que aprendemos com o iFood (suporte, 18/11/2026)
- O iFood responde **202** e processa depois. **Não existe endpoint de consulta** do resultado: o 202 é a confirmação.
- Para **groceries** só se usa a API **Item**. O **Catalog v2 fica com 0 produtos**: é esperado.
- O menu "Logs da API, HTTP Requests" do portal pode não mostrar as chamadas de ingestion. A validação é feita **por chamado**.
- Vídeos de homologação: grave a tela do painel fazendo POST (produto novo), PATCH (preço, promoção e estoque) e a carga `reset=true`.
  O portal limita o tamanho dos anexos: reduza os vídeos (cerca de 500 KB) ou envie **links** (Drive) no próprio chamado.

### Estado da homologação
- Módulo a homologar: **somente Item**. Periodicidade informada: **até 3 vezes ao dia** (PATCH) e sob demanda para produtos novos.
- Chamado de homologação **#36553470**: vídeos recebidos e registrados como evidência; caso em **validação técnica** pelo iFood.
- Chamado de dúvida técnica **#36555112**: respondido (ver acima).
- Falta: aguardar a validação; se o iFood pedir outro cenário ou evidência, grave de novo pelo painel (loja de teste).

### Quando a homologação for aprovada
1. Confirme no portal que o aplicativo **"Casa de Embalagens Cangaiba - Catalogo"** está homologado para o módulo Item.
2. Na tela **iFood (API)**, salve as chaves da **loja real** (e o ID da loja real) e use **Só testar**.
3. Liberar o envio para a loja real exige uma mudança no código (hoje `enviar_ifood_item` recusa qualquer ambiente que não seja o de teste).
   Faça isso com cuidado: comece por **1 produto**, confira a prévia e **nunca use a carga completa** (`reset=true`) na loja real.

## Acesso à Nuvemshop

Copie `config.exemplo.json` para `config.json` e cole o token (a senha é criada por `definir-senha.bat`). O arquivo fica só neste computador.
Sem ele o painel funciona normalmente, só não relê a Nuvemshop.

## Onde ficam os dados

Tudo em `banco.db`, nesta pasta. Para fazer backup, copie esse arquivo.


## Backup fora do computador

O painel já guarda uma cópia do banco por dia na pasta `backups` (as 14 mais recentes). Isso não protege se o computador queimar ou for roubado. Para guardar uma **segunda cópia em outro lugar**, abra o `config.json` e acrescente uma linha com a pasta de destino (use `/` ou `\\` duplas no Windows):

```json
"pasta_backup_extra": "G:/Meu Drive/backup-painel"
```

Pode ser a pasta do Google Drive/OneDrive no computador (o próprio programa deles envia para a nuvem) ou um pendrive/HD externo (ex.: `E:/backup-painel`). Guarda as 30 cópias mais recentes. Depois de salvar, feche a janela preta e abra o `iniciar.bat`.

Na tela inicial aparece um aviso amarelo enquanto não estiver configurado ou se a pasta não responder, e o rodapé mostra a data do último backup externo.

Para **restaurar**: feche o painel, copie um arquivo `banco_AAAAMMDD.db` da pasta de backup para a pasta do painel, renomeie para `banco.db` (guarde o antigo antes) e abra o `iniciar.bat`.
