# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## O que é este repositório

Site estático, sem build, sem dependências instaladas e sem testes. Tudo é HTML
escrito à mão, com `<style>` e `<script>` inline no próprio arquivo. Não existe
`package.json`, bundler, linter ou framework — não introduza nenhum sem pedido
explícito.

**Todo o conteúdo e as mensagens de commit são em português (pt-BR).**

Dois produtos diferentes convivem aqui:

| Caminho | O que é | Marca |
|---|---|---|
| `index.html` + imagens na raiz | Landing page da **Moara** (vende sites e landing pages) | Moara |
| `modelo-proposta/`, `proposta-vibe-petz/` | Propostas comerciais da **Humanize** | Humanize |
| `PADRAO-PROPOSTAS.md`, `padrao-propostas.html` | O padrão que rege as propostas | Humanize |

São marcas distintas, com identidade visual, tom e **WhatsApp diferentes**:

- Moara → `wa.me/5511945409653`
- Humanize → `wa.me/5511914894352`

Nunca troque um pelo outro, e nunca aplique as cores/tipografia de um lado no outro.

## Comandos

Não há build nem testes. Para ver as páginas:

```sh
python3 -m http.server 8000      # abre http://localhost:8000/ (Moara)
                                 #      http://localhost:8000/proposta-vibe-petz/
```

Gerar o banner de valores avulsos (1080×1350) a partir do HTML:

```sh
chromium --headless --screenshot=banner-valores-avulsos.png \
         --window-size=1080,1437 banner-valores-avulsos.html
# depois recortar para 1080×1350
```

Recomprimir vídeo de mockup antes de commitar (ver §7 do PADRAO):

```sh
ffmpeg -i entrada.mp4 -vf "crop=L:A:X:Y,scale=540:-2" \
       -c:v libx264 -crf 28 -preset slow -an -movflags +faststart saida.mp4
```

Publicação: a raiz vai para a Vercel (`landing-pages-moara.vercel.app`); as
propostas, para GitHub Pages do repositório de cada cliente.

## Landing page da Moara (`index.html`)

Arquivo único de ~1.560 linhas: `<style>` nas linhas 33–661, markup até ~1.138,
`<script>` de 1.141 a 1.551 e, depois dele, o botão flutuante do WhatsApp
(`.whatsapp-float`). Dependências externas: Google Fonts (DM Sans,
Manrope) e GSAP 3 + ScrollTrigger via jsDelivr — não há fallback se a CDN cair.

Identidade: fundo `--ink:#090b14`, acentos `--purple:#7c3aed` e `--lime:#b7ff3c`,
seções claras com `.light`. Ordem das seções: hero → público → problema →
benefícios → processo → projetos → planos → mensalidade → promoção → confiança →
dúvidas → CTA final.

### Três pontos que exigem edição manual coordenada

1. **Vitrine do hero (notebook + celular).** Três listas paralelas precisam ter o
   mesmo tamanho e a mesma ordem: os `<img class="desktop-screen-img">`, os
   `<img class="mobile-screen-img">` e o array `projectNames` no script. Se
   divergirem, a rotação automática é abortada (`desktopScreens.length !==
   mobileScreens.length`) e a vitrine congela.
2. **Placar de vagas da promoção.** O número é trocado à mão no HTML:
   `#vagas-restantes` (texto) e `data-total`. O script só desenha a barra e, em
   zero, troca o rótulo para "Vagas esgotadas".
3. **Ano do rodapé** é preenchido por JS (`#year`) — não escreva o ano no HTML.

### Animações

GSAP com `ScrollTrigger.matchMedia`, nos breakpoints 650/651px (parallax do hero)
e 950/951px (pin da seção de projetos). Regra que já custou um bug: cards animados
devem nascer **visíveis** e só ganhar movimento — se o ScrollTrigger falhar, nada
pode sumir da página. Os botões do hero têm `opacity/visibility/transform`
forçados com `!important` pelo mesmo motivo.

### Imagens

Todas na raiz, achatadas, sem subpasta. Os prints de projeto (`site-*-desktop.png`
/ `site-*-mobile.png`) pesam 1,5–2 MB cada e somam ~24 MB no repositório; ao
acrescentar projetos, prefira `.webp` como em `portfolio-mimo-*.webp`.

## Propostas da Humanize

**Leia `PADRAO-PROPOSTAS.md` antes de criar ou editar qualquer proposta.** Ele é a
fonte de verdade: estrutura das seções, design system (azul/laranja fixos, navy
calibrável), componentes, textos institucionais que não mudam, valores de
referência, checklist de envio e uma lista de armadilhas já conhecidas.

`padrao-propostas.html` é a mesma coisa em página navegável (sem `<!DOCTYPE>`,
nasceu como artifact). **Os dois andam juntos** — ao mudar o padrão, atualize os
dois arquivos.

Fluxo: copiar `modelo-proposta/`, trocar tudo entre `[[ ]]` (`[[CLIENTE]]`,
`[[BAIRRO]]`, `[[UF]]`, `[[PÚBLICO]]`, `[[OBJETIVO PRINCIPAL]]`), colocar as
imagens e ajustar pacotes. `proposta-vibe-petz/` é o modelo preenchido de
referência (difere do modelo em ~50 linhas) e traz também os dois extras de
valores avulsos (`valores-avulsos.html` + `banner-valores-avulsos.html`).

Restrições que a prática impôs:

- Proposta é documento que o cliente assina: **nunca invente preço, endereço, @ ou
  número**. Se o dado não veio, pergunte.
- Largura `max-width:520px` e fonte do sistema: a proposta é lida no celular, no 4G.
  Mire em menos de 4 MB por pasta.
- Arquivos da proposta sempre na mesma pasta do `index.html` — subpasta já causou
  404 na Vercel.
- Molduras de celular vêm do CSS (`.phone`); as imagens entram sem moldura.
- Ícones são `<symbol>` SVG dentro de `<defs>`, com `fill:none; stroke:currentColor`.

Duplicação conhecida (sinalizada no próprio PADRAO, ainda sem decisão): cada
proposta existe como pasta aqui **e** como repositório próprio do cliente. Ao mexer
em uma proposta, confirme qual cópia é a oficial antes de editar.
