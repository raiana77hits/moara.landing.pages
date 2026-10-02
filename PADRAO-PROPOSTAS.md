# Padrão de propostas comerciais · Humanize

Toda proposta nova segue a estrutura da `proposta-vibe-petz/`. Para criar uma nova:
copie a pasta para `proposta-<cliente>/`, troque textos, imagens e valores. Não mude o layout.

## Arquivos da pasta
- `index.html`: página única, estilo celular (largura máx. 520px), pronta para imprimir em A4
- `logo-humanize.png`: logo oficial da Humanize (sempre o mesmo)
- `logo-<cliente>.png`: logo do cliente recortado em círculo (300×300, fundo transparente)
- `fachada-<cliente>.jpg`: foto da fachada inteira e nítida, sem textos de story por cima
- `cardapio-insta-delivery.jpg` / `instagram-simulacao.jpg`: prints de simulação (sem barra de status/navegador)

## Identidade visual (não alterar)
- Fundo azul-marinho `#050b1e → #0c1a36`, seções claras `#f7f9fc` / branco
- Destaques azul `#1ea7ff`/`#4fc3ff` e laranja `#ff7a30`/`#ffa15c`
- Fonte Segoe UI / Arial; botão WhatsApp verde `#25D366`
- Contatos: WhatsApp (11) 91489-4352 · humanizemktdigital@gmail.com · @humanizemktdigital

## Ordem das seções
1. **Capa (escura)**: logo Humanize à esquerda, logo do cliente à direita. Selo "Proposta comercial",
   título "Marketing Digital para a **<Cliente>**", texto padrão da Humanize, foto da fachada com legenda
   "<Cliente> · <endereço> · <bairro>, <cidade>", e **Diagnóstico rápido** com 3 tópicos.
2. **Serviços incluídos (branca)**: 3 a 4 cards com ícone (ex.: cardápio digital na Insta Delivery,
   gerenciamento de redes sociais, gestão de tráfego pago). Sem cards de quantidade de vídeos/visitas.
3. **Material de apoio (clara)**: dois celulares lado a lado: simulação do Instagram e do cardápio/site.
4. **Investimento (escura)**: "Escolha o seu plano", 3 pacotes do mais barato ao mais caro;
   o último é o **Recomendado** (borda laranja + selo). Sem valores "contratando separado" nem tabela avulsa.
   Depois as condições: pagamento antecipado na assinatura, assinatura pelo Gov.br, renovação automática 12 meses.
5. **Chamada final**: caixa "Vamos aprovar e dar início à parceria?" com botão WhatsApp e contatos,
   rodapé e botão fixo "Falar com a Humanize agora".

## Pacotes de referência (Vibe Petz, out/2026)
| Pacote | Conteúdo | Valor |
|---|---|---|
| 1 · Publi avulsa | 3 vídeos gravados e editados, gravação em dia combinado | R$ 350 · pagamento único |
| 2 · Cardápio + Tráfego + Vídeos | Cardápio Insta Delivery com gerenciamento e suporte, tráfego pago, 10 vídeos/mês prontos e editáveis (cliente publica) | R$ 1.200/mês |
| 3 · Pacote Completo (Recomendado) | Cardápio Insta Delivery, gerenciamento de redes, 15 vídeos/mês gravados e postados, tráfego pago, 2 visitas/mês | R$ 1.550/mês |

Valores avulsos internos (não mostrar na proposta): cardápio R$ 600, tráfego R$ 750/mês, redes sociais R$ 1.500/mês.

## O que pedir ao cliente/vendedor antes de começar
1. Nome, segmento, endereço e @ do Instagram
2. Logo, foto da fachada e prints de simulação (Instagram e cardápio/site)
3. Serviços e valores dos 3 pacotes
4. Nota no Google / horário, se houver, para o diagnóstico

## Publicação
- Commit na pasta `proposta-<cliente>/` e push
- Publicar como artefato (link compartilhável) e/ou na Vercel com Root Directory `proposta-<cliente>`
