# Política de segurança

## Arquivos que não devem ser publicados

- `config/settings.json` e cópias temporárias;
- caminhos internos ou endereços de servidores;
- vídeos de entrada, saída, processados, erros ou quarentena;
- presets proprietários;
- executáveis do FFmpeg e outros binários;
- logs e históricos de produção;
- senhas de instalador;
- pacotes privados de atualização.

O `.gitignore` cobre os principais arquivos locais, mas cada alteração deve ser
revisada antes de um commit.

## Relato de vulnerabilidades

Não publique credenciais, caminhos internos, arquivos de clientes ou detalhes
exploráveis em uma issue pública.

Use o recurso **Security Advisories** do GitHub quando disponível. Caso ele não
esteja habilitado, abra uma issue sem dados sensíveis solicitando um canal
privado de contato.

## Escopo

As correções de segurança são mantidas para a versão mais recente publicada.
Builds antigas devem ser atualizadas antes de uma análise.
