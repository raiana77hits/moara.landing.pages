# Painel de Estoque · VibePetz

Cadastro central dos produtos da VibePetz, com a situação de cada um na Nuvemshop, no iFood e na
99Food. Roda só neste computador. **Não altera nada nas lojas online nem no Gestor Z.**

É o mesmo painel da Casa de Embalagens, em pasta separada: **banco de dados próprio, porta própria
e cores próprias**. Os dois podem ficar abertos ao mesmo tempo; um não enxerga o outro.

| | Casa de Embalagens | VibePetz |
|---|---|---|
| Pasta | `painel-estoque` | `painel-vibepetz` |
| Endereço | http://127.0.0.1:8765 | **http://127.0.0.1:8766** |
| Banco | o `banco.db` da pasta dela | o `banco.db` desta pasta |
| Celular (Tailscale) | `https://...ts.net` | `https://...ts.net:8443` |

## Como abrir (Windows)

1. O Python já está instalado por causa do outro painel. Se precisar de novo:
   https://www.python.org/downloads/ (marque "Add python.exe to PATH").
2. Dê dois cliques em `iniciar.bat`.
3. O painel abre no navegador em http://127.0.0.1:8766. Deixe a janela preta aberta enquanto usa.

Se aparecer "porta ocupada", é porque este painel já está aberto em outra janela preta. Feche
todas as janelas pretas **deste** painel e abra o `iniciar.bat` de novo.

## O arquivo `marca.json` (nome, cores, porta)

Tudo o que é "a cara" deste painel está em **um arquivo só**, o `marca.json`. Nada de nome de loja
escrito no meio do programa. Abra no Bloco de Notas para mudar:

```json
{
  "loja": "VibePetz",
  "titulo": "Painel de Estoque",
  "porta": 8766,
  "divisor_apps": 0.70,
  "canais": ["nuvemshop", "ifood", "99food"],
  "cores": { "principal": "#1E3FAE", "escuro": "#152E86", "suave": "#E6ECFB", "apoio": "#B07A00" }
}
```

- **loja / titulo**: aparecem no topo da tela, na aba do navegador e na tela de senha.
- **porta**: o número do endereço. Só mude se precisar; tem que ser diferente da do outro painel.
- **divisor_apps**: a conta do preço dos apps. 0,70 significa *preço do Gestor Z ÷ 0,70*.
- **canais**: quais lojas online este painel acompanha. Pode tirar o que a VibePetz não usar.
- **cores**: use o formato `#RRGGBB`.

Depois de salvar: feche a janela preta, abra o `iniciar.bat` de novo e dê **Ctrl+F5** no navegador.
Se escrever algo errado (uma cor inválida, por exemplo), o painel **não quebra**: ele usa o valor
padrão e escreve o aviso na janela preta.

## O que já funciona e o que falta ligar

Funciona desde agora: produtos, conferência pelo celular, notas e vendas, estoque estimado,
prioridades, cruzamento de preços, planilhas e as telas de envio (com prévia).

Falta, e fazemos juntas, uma coisa de cada vez:

1. **Importar o cadastro da VibePetz** (planilha do Gestor Z: CSV ou XLSX) em
   *Ferramentas → Atualizar dados*. Enquanto não importar, o painel fica vazio — é o esperado.
2. **Nuvemshop**: colar o token e o número da loja da VibePetz no `config.json`.
3. **iFood (API)**: a VibePetz precisa do **aplicativo dela** no Portal do Parceiro e da
   homologação do módulo Item. As chaves da Casa de Embalagens **não servem** aqui.
4. **99Food**: planilhas de catálogo.

**A pasta `static/modelos` não veio neste pacote** (são as planilhas-modelo do iFood e da 99).
Quando for usar *Planilhas dos apps*, copie a pasta `static\modelos` do painel da Casa de
Embalagens para a pasta `static` deste painel. Se esquecer, o painel avisa em português.

## Senha e acesso pelo celular

A senha deste painel é **separada** da do outro.

1. **Senha:** dois cliques em `definir-senha.bat`, mínimo de 8 caracteres.
2. **Túnel:** com o painel aberto, dois cliques em `conectar-celular.bat`. Ele usa a porta
   **8443**, porque a 443 já é do outro painel. O endereço termina com `:8443`.
3. **No celular**, com o Tailscale ligado, abra esse endereço e some `/conferir` no final.
   Depois use "Adicionar à tela inicial" para virar um ícone separado do outro painel.

O resto (câmera só com https, computador ligado, não sair da conta do Windows) é igual ao outro
painel.

## Regras que o painel respeita sempre

- **Só vale o estoque contado ou estimado do painel.** Nenhum número é inventado.
- **Preço oficial = Gestor Z** (`preco_gz`), que só muda quando você importa o cadastro.
  Esperado: Nuvemshop = preço do Gestor Z; iFood e 99Food = preço do Gestor Z ÷ 0,70.
- **Nada vai para loja real sem prévia e sem você confirmar.** No iFood real: o primeiro envio é
  de **1 produto**, depois no máximo 100 por vez, é preciso **digitar ENVIAR**, e a **carga
  completa (reset) nunca** é feita na loja real. Cada envio grava backup em `backups/`.
- Nuvemshop: nunca apaga nada; produto novo nasce **não publicado**; produto com variações não é
  tocado; por padrão é só leitura.
- O iFood responde **202** e processa depois: a conferência é no Portal do Parceiro.
- Chaves e senhas ficam só no `config.json` deste computador, nunca no repositório.

## Backup

O painel guarda uma cópia do banco por dia em `backups` (as 14 mais recentes). Isso **não** protege
se o computador queimar. Para a segunda cópia, abra o `config.json` e acrescente:

```json
"pasta_backup_extra": "G:/Meu Drive/backup-painel-vibepetz"
```

Use uma pasta **diferente** da do outro painel, para um não cobrir o backup do outro.
Para restaurar: feche o painel, copie um `banco_AAAAMMDD.db` da pasta de backup para esta pasta,
renomeie para `banco.db` e abra o `iniciar.bat`.

## Abrir sozinho quando o computador liga

Igual ao outro painel, usando o `iniciar-com-windows.bat` **desta** pasta: botão direito, Criar
atalho; Windows + R, `shell:startup`, Enter; arraste o atalho para lá; Propriedades, Executar
"Minimizada". Dá para deixar os dois painéis iniciando juntos.

## Onde ficam os dados

Tudo em `banco.db`, nesta pasta, mais as fotos em `fotos` e as cópias em `backups`.
