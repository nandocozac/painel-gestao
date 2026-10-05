---
name: db-backup-maintenance
description: >-
  Use this skill whenever performing database migrations, schema alterations,
  maintenance tasks, or routine backups for the SQLite database in the Painel de Gestão.
  It executes atomic backups without locking concurrent writes and verifies physical file integrity.
---

# DB Backup & Maintenance (Backup e Manutenção do Banco)

Esta skill fornece procedimentos seguros e scripts automatizados para garantir a integridade e a preservação do banco de dados SQLite (`instance/painel_gestao.db`).

## Quando usar esta Skill:
- Antes de rodar migrações manuais ou scripts de alteração de tabelas (`ALTER TABLE`).
- Antes de atualizar o sistema em produção.
- Para verificar se o banco de dados apresenta qualquer sinal de corrupção ou travamento.

## Procedimento para Realizar Backup:

1. Execute o script de backup atômico:
   ```powershell
   .\venv\Scripts\python.exe .agents/skills/db-backup-maintenance/scripts/backup_database.py
   ```

2. O que o script realiza:
   - Executa `PRAGMA integrity_check` para validar se a estrutura do banco está sadia.
   - Utiliza a API `sqlite3.backup()` para criar uma cópia ponto-no-tempo consistente, mesmo que usuários estejam salvando ordens de serviço no mesmo instante.
   - Armazena o snapshot na pasta `instance/backups/painel_gestao_backup_AAAAMMDD_HHMMSS.db`.

## Procedimento de Restauração:
- Pelo Painel Master: O Dono Master pode acessar `/admin/empresas` e utilizar o card **Manutenção e Backup do Banco de Dados** para baixar ou enviar um arquivo de backup diretamente pela interface gráfica.
- Manualmente via terminal:
  ```powershell
  copy instance\backups\painel_gestao_backup_YYYYMMDD_HHMMSS.db instance\painel_gestao.db
  ```
