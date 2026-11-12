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

## Acesso à Nuvemshop

Copie `config.exemplo.json` para `config.json` e cole o token (a senha é criada por `definir-senha.bat`). O arquivo fica só neste computador.
Sem ele o painel funciona normalmente, só não relê a Nuvemshop.

## Onde ficam os dados

Tudo em `banco.db`, nesta pasta. Para fazer backup, copie esse arquivo.
