# Padrão de Propostas Comerciais · Humanize

Como montar uma proposta comercial da Humanize. Tudo aqui foi extraído das
propostas que já existem — não é um ideal, é o que está no ar.

**Última atualização:** 13/10/2026 · **Propostas vivas:** Vibe Petz, Chay Holanda, Espaço Granada

> Este arquivo junta as duas versões que estavam em branches separadas
> (`claude/admiring-galileo-tg8hle` e `claude/gallant-rubin-nzn71u`).

---

## 1. Como começar uma proposta nova

Não comece do zero. **Copie o `index.html` da proposta mais recente** e troque o
conteúdo. Hoje a referência é o Espaço Granada, que é a mais completa.

| Proposta | Repositório | O que ela acrescentou ao padrão |
|---|---|---|
| Vibe Petz | `proposta-Vibe-pets` | Estrutura original: hero, serviços, material de apoio, investimento |
| Chay Holanda | `proposta-para-a-Corretora-Chay-Holanda` | Material de apoio com dois celulares lado a lado |
| **Espaço Granada** | `proposta-comercial-casa-de-piscina-espaco-granada` | Seção de estrutura, vídeo no mockup, grade de ícones |

### Repositório por cliente

Cada proposta tem o **seu próprio repositório**, com os arquivos na **raiz** (não
em subpasta) e na branch `main`.

> ⚠️ **Conferir:** neste repositório (`moara.landing.pages`) as propostas também
> existem como pastas (`proposta-vibe-petz/`,
> `proposta-comercial-casa-de-piscina-espaco-granada/`). Defina qual é o lugar
> oficial e apague a cópia que sobrar.

Nome do repositório: minúsculas, hífens, **sem acento e sem espaço** — o GitHub não
aceita no caminho da URL e o link quebra ao ser colado no WhatsApp.

```
proposta-comercial-casa-de-piscina-espaco-granada   ✅
proposta comercial casa de piscina espaço granada   ❌
```

---

## 2. Estrutura da página

Seções nesta ordem. O fundo alterna para dar ritmo à rolagem:

| # | Seção | Fundo | Obrigatória |
|---|---|---|---|
| 1 | Hero + diagnóstico | `dark` | sim |
| 2 | Estrutura do negócio | `light` | não — só quando o cliente tem espaço físico para mostrar |
| 3 | Serviços incluídos | `white` | sim |
| 4 | Material de apoio | `light` | sim |
| 5 | Investimento | `dark` | sim |
| 6 | Rodapé + CTA fixo | `navy-900` | sim |

### 1. Hero

- **Topo:** wordmark `HUMANIZE / marketing digital` à esquerda, logo do cliente à direita (círculo de 44px)
- **Pill laranja:** `Proposta comercial`
- **H1:** `Marketing Digital para o/a <Cliente>` — o nome do cliente em `.accent`
- **Lead:** o texto institucional da Humanize (ver §5, é fixo)
- **Foto:** fachada ou o melhor ângulo do negócio, com legenda = endereço
- **Diagnóstico rápido:** 3 a 4 bullets

O diagnóstico é a parte mais importante da proposta. Ele precisa citar **números
reais do cliente** e apontar o buraco entre o que ele já tem e o que ele colhe.
No Espaço Granada: *"6.494 seguidores, mas apenas 3 publicações"* — audiência
pronta, sem conteúdo que converta. É isso que faz o preço parecer barato.

### 2. Estrutura (opcional)

Grade de dois por dois com ícone e nome do que o espaço oferece. Use quando o
negócio é um lugar (salão, loja, clínica) e o argumento é *"não falta estrutura,
falta quem veja"*.

### 3. Serviços incluídos

Eyebrow `Escopo da parceria`, H2 `Serviços incluídos`, um card por serviço
(ícone + título + uma frase). Alterne o `icon-badge` entre azul e laranja.

### 4. Material de apoio

Eyebrow `Material de apoio`, H2 `Como fica o Instagram e a landing page`.

Dois celulares lado a lado, legenda curta embaixo de cada:

- **Esquerda:** simulação do Instagram → *"Instagram organizado e com identidade visual"*
- **Direita:** simulação do site → *"Landing page com contato no WhatsApp"*

Esta seção mostra o **depois**, não o antes. O antes já está no diagnóstico, em texto.

### 5. Investimento

Eyebrow `Proposta de investimento`. H2 `Escolha o seu plano` (vários planos) ou
`Seu plano` (um só).

Um `.plan` por pacote. O recomendado leva `.plan.best` + `<span class="badge">Recomendado</span>`.
Abaixo, a tabela de condições comerciais (§5) e o bloco de CTA.

- Pacotes do mais barato ao mais caro; o último é o **Recomendado** (borda laranja + selo)
- Sem valores "contratando separado" nem tabela avulsa na proposta principal (ver §6)
- Sem cards de quantidade de vídeos/visitas na seção de serviços: isso fica nos pacotes
- Quem não vende por delivery (corretor, clínica, serviço, espaço de eventos): o
  cardápio Insta Delivery vira **landing page** com botão para o WhatsApp

#### Pacotes de referência

Vibe Petz (out/2026):

| Pacote | Conteúdo | Valor |
|---|---|---|
| 1 · Publi avulsa | 3 vídeos gravados e editados, gravação em dia combinado | R$ 350 · pagamento único |
| 2 · Cardápio + Tráfego + Vídeos | Cardápio Insta Delivery com gerenciamento e suporte, tráfego pago, 10 vídeos/mês prontos e editáveis (cliente publica) | R$ 1.200/mês |
| 3 · Pacote Completo (Recomendado) | Cardápio Insta Delivery, gerenciamento de redes, 15 vídeos/mês gravados e postados, tráfego pago, 2 visitas/mês | R$ 1.550/mês |

Plano único (Chay Holanda, out/2026): tráfego pago, redes sociais, captação de
conteúdo e 1 landing page, R$ 1.200/mês.

---

## 3. Design system

**Não mexa nos acentos.** Azul e laranja são da Humanize e são o que faz as
propostas parecerem da mesma casa. Só o navy pode ser calibrado para o cliente.

```css
/* fixo — identidade Humanize */
--blue-500:#1ea7ff;  --blue-400:#4fc3ff;  --blue-glow:#00c3ff;
--orange-500:#ff7a30; --orange-400:#ffa15c;
--white:#ffffff;      --gray-050:#f7f9fc;  --gray-100:#eef1f7;
--gray-300:#cfd6e4;   --gray-600:#5b6478;  --dark-text:#0b1633;

/* calibrável por cliente (padrão abaixo) */
--navy-900:#050b1e;  --navy-800:#081226;  --navy-700:#0c1a36;
```

Chay Holanda usa `--navy-900:#040b2a` (mais azulado, puxando para o dourado da
marca). Vibe Petz e Espaço Granada usam o padrão.

- **Fonte:** `'Segoe UI', Arial, Helvetica, sans-serif` — sem webfont, carrega instantâneo no 4G
- **Largura:** `.wrap` com `max-width:520px` — a proposta é lida no celular, sempre
- **Impressão:** `@page{size:A4;margin:0}`, CTA fixo escondido, `break-inside:avoid` nas seções
- **Ícones:** `<symbol>` SVG inline dentro de um `<defs>`, traço, nunca preenchimento

---

## 4. Componentes

| Classe | Para quê |
|---|---|
| `.dark` `.light` `.white` | fundo da seção |
| `.glow` `.lines` | textura do fundo escuro |
| `.pill` `.eyebrow` `.divider` | rótulos e separador acima dos títulos |
| `.hero-photo` + `.hero-cap` | foto do hero com legenda |
| `ul.bullets` | diagnóstico |
| `.feat` | grade de estrutura |
| `.cards` / `.card` + `.icon-badge` | serviços |
| `.phones` / `.phone` + `.phone-cap` | mockups de celular |
| `.plans` / `.plan` (+ `.best`) | pacotes |
| `table.svc` | condições comerciais |
| `.cta-box` `.cta-btn` `.sticky-cta` | chamadas para WhatsApp |

### Celulares

A moldura vem do CSS (`.phone`). Então **as imagens e vídeos entram sem moldura**
— só o conteúdo da tela. Mockups prontos que já vêm com um celular desenhado
precisam ser recortados, ou vira celular dentro de celular.

Os dois celulares ficam da mesma altura via `aspect-ratio:9/17` + `object-fit:cover`.

---

## 5. Textos que não mudam

**Contatos da Humanize** (bloco de CTA e rodapé):

```
WhatsApp   https://wa.me/5511914894352
Telefone   (11) 91489-4352
E-mail     humanizemktdigital@gmail.com
Instagram  @humanizemktdigital
Rodapé     Humanize · Marketing Digital
```

**Lead institucional** (hero):

> A Humanize é uma agência de marketing digital que ajuda empresas a transformar
> presença online em vendas, com estratégia, conteúdo humanizado e gestão de
> tráfego pago.

**Condições comerciais** (`table.svc`, no fim do investimento):

1. Primeiro pagamento antecipado, no momento da assinatura
2. Assinatura digital do contrato pelo Gov.br
3. Renovação automática por 12 meses, caso nenhuma das partes manifeste interesse em encerrar

Quando o pacote inclui tráfego pago, acrescente:

4. Verba de anúncios paga à parte, direto às plataformas, definida junto com o cliente

---

## 6. Valores avulsos (atualizado out/2026)

Não entram na proposta principal. Só quando o cliente perguntar por serviços separados.

| Serviço | Valor |
|---|---|
| Criação da plataforma (cardápio Insta Delivery / landing page) | R$ 600 · pagamento único |
| Gestão de tráfego pago | R$ 900/mês |
| Criativos para o tráfego (vídeos) | R$ 350 por pacote |
| Gerenciamento de redes sociais | R$ 1.500/mês |

Quando o cliente pedir os avulsos, criar junto da proposta:

- `valores-avulsos.html`: mesma identidade, uma seção escura com os 4 serviços em
  cards (valor + "pagamento único"/"por mês"), quadro laranja "Vale comparar" com o
  Pacote Completo, link de volta para `index.html`, condições e chamada WhatsApp
- `banner-valores-avulsos.png` (1080×1350, feed/WhatsApp), gerado a partir de
  `banner-valores-avulsos.html` com Chromium headless (`--window-size=1080,1437`,
  depois recortar para 1080×1350)
- Modelo: `proposta-vibe-petz/valores-avulsos.html` e `proposta-vibe-petz/banner-valores-avulsos.html`

---

## 7. Arquivos

Nomeie pelo que a imagem é, com o cliente no fim:

```
logo-<cliente>.png              logo, recortado em círculo com fundo transparente
foto-<cliente>.jpg              foto do hero
mockup-instagram-<cliente>.jpg  simulação do Instagram, sem moldura
<cliente>-landing-mockup.mp4    vídeo da landing page, sem moldura
site-*.jpg                      imagens da simulação animada do site (se houver)
poster-<cliente>.jpg            primeiro quadro do vídeo
```

### Peso

A proposta chega pelo WhatsApp e abre no 4G. **Mire em menos de 4 MB no total.**

- Fotos: largura de 1000px basta para o hero
- Mockups de celular: 640px de largura
- **Vídeo: sempre recomprima.** Direto do celular vem com 1080×1920 e 10 MB ou mais.
  Recorte a tela, reescale para 540px, tire o áudio:

  ```sh
  ffmpeg -i entrada.mp4 -vf "crop=L:A:X:Y,scale=540:-2" \
         -c:v libx264 -crf 28 -preset slow -an -movflags +faststart saida.mp4
  ```

  No Espaço Granada isso levou o vídeo de 11 MB para 2,5 MB.

- Gere sempre o `poster`: é o que aparece antes de carregar e na versão impressa,
  onde vídeo não renderiza.

O vídeo entra assim:

```html
<video src="cliente-landing-mockup.mp4" poster="poster-cliente.jpg"
       autoplay muted loop playsinline preload="metadata"></video>
```

`playsinline` é obrigatório — sem ele o iPhone abre o vídeo em tela cheia sozinho.

**Simulação animada do site** (alternativa ao vídeo, usada na Chay Holanda): embutida
direto no `index.html` com `<iframe srcdoc>`, imagens soltas como `site-*.jpg`.
**Não use subpasta**: a Vercel não achou `site/` e deu 404 dentro do celular.

### Landing page do cliente

Quando o pacote inclui landing page, ela vira um site à parte (não fica dentro da
proposta). Exemplo: `espaco-granada.html` + pasta `granada/` em `moara.landing.pages`,
montada a partir do banner do cliente. Regras que valeram ali:

- Usar só informações que o cliente forneceu (banner, Instagram, site). O que estiver
  ilegível ou faltando (capacidade, endereço, horários) fica sinalizado, não inventado
- Fotos recortadas uma a uma, sem textos do banner por cima, `object-fit: cover`
- Promoção com data (ex.: "mês de agosto") precisa ser revista antes de publicar

---

## 8. Antes de enviar

- [ ] Todo `src=` e `poster=` aponta para arquivo que existe
- [ ] Nada de subpasta para a simulação do site
- [ ] Nenhum arquivo sobrando na pasta sem ser usado na página
- [ ] Os valores dos pacotes conferem com o que foi combinado — **nunca invente preço**
- [ ] Nome do cliente escrito certo, com acento, em todo lugar
- [ ] Endereço e telefone do cliente conferidos
- [ ] Os dois botões de WhatsApp apontam para a Humanize, não para o cliente
- [ ] Abre no celular sem rolagem lateral
- [ ] Total da pasta abaixo de 4 MB
- [ ] Arquivos na raiz do repositório do cliente, na branch `main`

### Link para o cliente

Ative o **GitHub Pages** no repositório (Settings → Pages → `main` → `/ (root)`).
O link fica:

```
https://raiana77hits.github.io/<nome-do-repositorio>/
```

É aberto, não pede login, e é o que se manda no WhatsApp.

Alternativas usadas antes: publicar como artefato (link compartilhável) ou na Vercel
com Root Directory apontando para a pasta da proposta.

### O que pedir ao cliente/vendedor antes de começar

1. Nome, segmento, endereço e @ do Instagram
2. Logo, foto da fachada (ou da pessoa, se não tiver loja) e prints/vídeo de simulação
3. Serviços e valores dos pacotes (ou do plano único)
4. Seguidores, publicações, nota no Google e horário, para o diagnóstico

---

## 9. Armadilhas já conhecidas

- **Documento dentro de documento.** O `index.html` da Vibe Petz tem um `<html>`
  completo dentro de outro, sobra de um upload antigo. Não copie isso: um arquivo,
  um `<!DOCTYPE html>`.
- **Ícone preto em vez de contornado.** SVG sem `fill:none; stroke:currentColor`
  renderiza como mancha preta. Todo bloco que usa ícone precisa dessas duas regras.
- **`<symbol>` fora do `<defs>`.** Também herdado da Vibe Petz. Funciona por sorte,
  mas é HTML inválido — mantenha todos dentro.
- **Preço inventado.** Se os valores não vieram, pergunte. Proposta é documento que
  vai para o cliente assinar.
- **Subpasta na simulação do site.** A Vercel não encontrou `site/` e o celular
  mostrou 404. Tudo na mesma pasta do `index.html`.
- **Duas versões deste arquivo.** Antes de editar, confira se não há outra branch
  mexendo nele — foi assim que os valores avulsos quase se perderam.
