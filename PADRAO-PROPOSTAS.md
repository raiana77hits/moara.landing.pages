# Padrão de propostas comerciais · Humanize

Toda proposta nova parte da pasta **`modelo-proposta/`**. Não mude o layout: troque só textos, imagens e valores.
Exemplo real já aprovado: `proposta-vibe-petz/` (out/2026).

## Passo a passo (15 minutos)
1. Copie `modelo-proposta/` para `proposta-<cliente>/` (nome em minúsculas, com hífen).
2. Coloque as 4 imagens com estes nomes exatos:
   - `logo-cliente.png`: logo recortado em círculo, 300×300, fundo transparente
   - `fachada.jpg`: fachada inteira e nítida
   - `simulacao-instagram.jpg`: print do Instagram simulado
   - `simulacao-cardapio.jpg`: print do cardápio/site simulado
3. Abra `index.html` e troque tudo que estiver entre `[[ ]]` (cliente, endereço, diagnóstico, público).
4. Confira os 3 pacotes e os valores com o vendedor.
5. Tire um print da página no celular e revise antes de mandar.
6. Commit, push e publique (artefato e/ou Vercel com Root Directory `proposta-<cliente>`).

## Checklist do que pedir ao vendedor
- [ ] Nome do cliente, segmento, endereço completo e @ do Instagram
- [ ] Logo em boa qualidade
- [ ] Foto da fachada **inteira**, de frente, de dia
- [ ] Prints de simulação: Instagram e cardápio (Insta Delivery) ou site
- [ ] Serviços e valores dos 3 pacotes (e se cada um é mensal ou pagamento único)
- [ ] Nota no Google e horário de funcionamento (para o diagnóstico)

## Estrutura da página (5 seções, nesta ordem)
1. **Capa (escura)**: logo Humanize à esquerda, logo do cliente à direita. Selo "Proposta comercial",
   título "Marketing Digital para a **[Cliente]**", texto padrão da Humanize, foto da fachada com legenda
   "[Cliente] · [endereço] · [bairro], [UF]" e **Diagnóstico rápido** com 3 tópicos:
   ponto forte, o que falta hoje e objetivo da parceria.
2. **Serviços incluídos (branca)**: 3 a 4 cards com ícone (cardápio digital na Insta Delivery,
   gerenciamento de redes sociais, gestão de tráfego pago).
3. **Material de apoio (clara)**: dois celulares lado a lado, do mesmo tamanho: Instagram e cardápio/site.
4. **Investimento (escura)**: "Escolha o seu plano", 3 pacotes do mais barato ao mais caro.
   O último é o **Recomendado** (borda laranja + selo).
5. **Condições e chamada final**: pagamento antecipado na assinatura, assinatura pelo Gov.br,
   renovação automática por 12 meses. Caixa "Vamos aprovar e dar início à parceria?" com WhatsApp,
   contatos e botão fixo "Falar com a Humanize agora".

## Pacotes padrão (valores de referência, out/2026)
| Pacote | Conteúdo | Valor |
|---|---|---|
| 1 · Publi avulsa | 3 vídeos gravados e editados, gravação em dia combinado | R$ 350 · pagamento único |
| 2 · Cardápio + Tráfego + Vídeos | Cardápio Insta Delivery com gerenciamento e suporte, tráfego pago, 10 vídeos/mês prontos e editáveis (cliente publica) | R$ 1.200/mês |
| 3 · Pacote Completo (Recomendado) | Cardápio Insta Delivery, gerenciamento de redes, 15 vídeos/mês gravados e postados, tráfego pago, 2 visitas/mês | R$ 1.550/mês |

Valores avulsos **internos** (nunca mostrar na proposta): cardápio R$ 600 · tráfego R$ 750/mês · redes sociais R$ 1.500/mês.
Clientes "diferenciados" podem receber desconto: ajuste só o valor do pacote, sem mostrar o preço cheio.

## Lições da prática (o que já deu errado e como evitar)
- **Fachada:** print de vídeo ou story fica borrado e cortado. Peça foto nítida e corte os adesivos de texto do story.
- **Sem comparação de preços:** "contratando separado / economia de R$ X" e tabela avulsa confundem
  (misturam valor único com mensal) e fazem o pacote do meio parecer melhor. Mostre só os 3 pacotes.
- **Ordem dos pacotes:** do mais barato ao Recomendado, para o completo fechar a seção em destaque.
- **Deixe claro a cobrança:** escreva "/mês" ou "pagamento único" em todo valor.
- **Insta Delivery é uma plataforma de cardápio digital**, não o Instagram. Descreva assim.
- **Quantidade de vídeos e visitas** aparece só dentro dos pacotes, não nos cards de serviço
  (cada pacote tem uma quantidade diferente).
- **Simulações reais vendem mais** que telas de exemplo: sempre use prints do cliente simulados.
- **Confira o @ do Instagram** no print (ex.: `vibe_petz`, e não `vibepetz`).
- **Logo Humanize oficial** (`logo-humanize.png`) sempre no topo; nunca o nome em texto.

## Identidade visual (não alterar)
- Fundo azul-marinho `#050b1e → #0c1a36`, seções claras `#f7f9fc` / branco
- Destaques azul `#1ea7ff`/`#4fc3ff` e laranja `#ff7a30`/`#ffa15c`; botão WhatsApp `#25D366`
- Página em formato de celular (até 520px), pronta para imprimir em A4
- Contatos: WhatsApp (11) 91489-4352 · humanizemktdigital@gmail.com · @humanizemktdigital

## Como pedir ao Claude
> Claude, cria uma proposta nova para [cliente] seguindo o PADRAO-PROPOSTAS.md (base: modelo-proposta).
> Segue logo, fachada, prints e os valores dos 3 pacotes.
