# Padrão de propostas comerciais · Humanize

Toda proposta nova segue a estrutura da `proposta-vibe-petz/`. Para criar uma nova:
copie a pasta para `proposta-<cliente>/`, troque textos, imagens e valores. Não mude o layout.

## Arquivos da pasta
- `index.html`: página única, estilo celular (largura máx. 520px), pronta para imprimir em A4
- `logo-humanize.png`: logo oficial da Humanize (sempre o mesmo)
- `logo-<cliente>.png`: logo do cliente recortado em círculo (300×300, fundo transparente)
- `fachada-<cliente>.jpg`: foto da fachada inteira e nítida, sem textos de story por cima.
  Sem loja física (corretor, autônomo): `foto-<cliente>.jpg`, uma foto boa da pessoa
- `cardapio-insta-delivery.jpg` (ou `site-simulacao.jpg`) / `instagram-simulacao.jpg`: prints de simulação (sem barra de status/navegador)
- `site/` (opcional): simulação animada do site, embutida num iframe dentro do celular e rolando sozinha

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
   Quem não vende por delivery (corretor, clínica, serviço): o cardápio vira **landing page** com botão para o WhatsApp.
3. **Material de apoio (clara)**: dois celulares lado a lado: simulação do Instagram e do cardápio/site.
   O site pode ser print ou a simulação animada (pasta `site/`).
4. **Investimento (escura)**: "Escolha o seu plano", 3 pacotes do mais barato ao mais caro;
   o último é o **Recomendado** (borda laranja + selo). Sem valores "contratando separado" nem tabela avulsa.
   Se houver só um plano: título "Seu plano", um card único com selo Recomendado.
   Depois as condições: pagamento antecipado na assinatura, assinatura pelo Gov.br, renovação automática 12 meses.
5. **Chamada final**: caixa "Vamos aprovar e dar início à parceria?" com botão WhatsApp e contatos,
   rodapé e botão fixo "Falar com a Humanize agora".

## Pacotes de referência (Vibe Petz, out/2026)
| Pacote | Conteúdo | Valor |
|---|---|---|
| 1 · Publi avulsa | 3 vídeos gravados e editados, gravação em dia combinado | R$ 350 · pagamento único |
| 2 · Cardápio + Tráfego + Vídeos | Cardápio Insta Delivery com gerenciamento e suporte, tráfego pago, 10 vídeos/mês prontos e editáveis (cliente publica) | R$ 1.200/mês |
| 3 · Pacote Completo (Recomendado) | Cardápio Insta Delivery, gerenciamento de redes, 15 vídeos/mês gravados e postados, tráfego pago, 2 visitas/mês | R$ 1.550/mês |

Plano único de referência (Chay Holanda, out/2026): tráfego pago, redes sociais, captação de conteúdo e 1 landing page, R$ 1.200/mês.

Valores avulsos internos (não mostrar na proposta): cardápio R$ 600, tráfego R$ 750/mês, redes sociais R$ 1.500/mês.

## O que pedir ao cliente/vendedor antes de começar
1. Nome, segmento, endereço e @ do Instagram
2. Logo, foto da fachada (ou da pessoa) e prints de simulação (Instagram e cardápio/site); link do artefato se o site for animado
3. Serviços e valores dos 3 pacotes (ou do plano único)
4. Nota no Google / horário, se houver, para o diagnóstico

## Publicação
- Commit na pasta `proposta-<cliente>/` e push
- Publicar como artefato (link compartilhável) e/ou na Vercel com Root Directory `proposta-<cliente>`

## Propostas já feitas
- `proposta-vibe-petz/`: pet shop com delivery, 3 pacotes
- `proposta-chay-holanda/`: corretora de imóveis, plano único, simulação animada do site
