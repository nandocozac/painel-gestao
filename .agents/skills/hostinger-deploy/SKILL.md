---
name: hostinger-deploy
description: >-
  Use this skill when preparing, testing, or publishing new releases of the Painel de Gestão
  to the Hostinger (LiteSpeed / Passenger WSGI) production environment. It guarantees zero data
  loss, verifies the presence of the .htaccess file, and ensures proper application restart.
---

# Hostinger Deploy (Publicação Segura na Hostinger)

Esta skill orienta o processo de deploy sem interrupções e sem risco de perda de dados das empresas cadastradas no servidor de produção.

## Princípio Fundamental:
> **NUNCA sobrescreva a pasta `instance/` ou o arquivo `painel_gestao.db` no servidor de produção com cópias locais de teste!** O banco em produção contém cadastros, ordens de serviço e clientes reais.

## Método 1: Atualização via Git (Recomendado)

1. No computador local, garanta que todos os testes passaram e faça o push:
   ```powershell
   git status
   git add .
   git commit -m "feat/fix: descricao da melhoria"
   git push origin main
   ```

2. No terminal SSH da Hostinger, navegue até a pasta da aplicação e puxe as alterações:
   ```bash
   cd ~/public_html/painel_gestao  # ou a pasta raiz da sua aplicação Python
   git pull origin main
   ```

3. Reinicie a aplicação Python no painel da Hostinger (ou crie o arquivo de trigger):
   ```bash
   mkdir -p tmp && touch tmp/restart.txt
   ```

---

## Método 2: Atualização via Pacote ZIP

1. Gere o pacote de publicação limpo e protegido:
   ```powershell
   .\venv\Scripts\python.exe .agents/skills/hostinger-deploy/scripts/prepare_deploy_package.py
   ```

2. Acesse o Gerenciador de Arquivos da Hostinger:
   - Faça o upload do arquivo `publicacao_hostinger.zip` gerado na raiz.
   - Extraia os arquivos substituindo os arquivos de código existentes.
   - *(Note que o zip não contém `instance/`, portanto o banco de dados dos clientes permanece 100% intacto).*

3. Reinicie a aplicação Python no painel Cloud/cPanel da Hostinger.

---

## Checklist de Verificação Pós-Deploy:
- [ ] O arquivo `.htaccess` está presente na raiz com as regras `CacheDisable public /` e `CacheDisable private /`.
- [ ] A aplicação responde com status `200` e carrega a tela de login.
- [ ] Ao logar, a empresa Master está ativa e com acesso aos módulos normais e ao painel `/admin/empresas`.
- [ ] Os cabeçalhos de resposta HTTP contêm `Cache-Control: no-cache, no-store, must-revalidate, max-age=0, private`.
