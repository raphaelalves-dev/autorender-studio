# Como contribuir

O AutoRender Studio é um projeto proprietário da Clicks da Serra. Sugestões,
relatos de falha e propostas de melhoria são bem-vindos.

## Antes de começar

1. Pesquise se já existe uma issue relacionada.
2. Abra uma issue descrevendo o comportamento atual e o resultado esperado.
3. Não inclua vídeos de clientes, presets, logs de produção ou configurações
   internas.
4. Aguarde o alinhamento antes de iniciar uma alteração extensa.

## Desenvolvimento

```powershell
git clone https://github.com/raphaelalves-dev/autorender-studio.git
cd autorender-studio
INSTALAR.bat
```

Execute os testes:

```powershell
python -m unittest discover -s tests -v
```

## Pull requests

- mantenha cada pull request focado em um único objetivo;
- inclua testes quando o comportamento for alterado;
- explique riscos e impactos no fluxo de produção;
- não adicione binários, presets, vídeos ou dados locais;
- confirme que a suíte automatizada passou.

O envio de uma contribuição não altera a licença nem a titularidade do projeto.
A incorporação depende de aprovação do mantenedor.
