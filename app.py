import os
import csv
import re
import time
from io import StringIO
from datetime import date, timedelta
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, Response, send_file, session, g
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import text
from database import db
from models import Empresa, Cliente, Veiculo, OrdemServico, Transacao, ItemOS

app = Flask(__name__)
app.config['SECRET_KEY'] = 'chave-segura-painel-interno-multiempresa-2026'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///painel_gestao.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

UPLOAD_FOLDER = os.path.join(app.root_path, 'static', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

db.init_app(app)


# --- MIGRAÇÃO AUTOMÁTICA MULTI-EMPRESA E ADMINISTRAÇÃO SAAS ---

def migrar_banco_multiempresa():
    with app.app_context():
        db.create_all()

        # 1. Garantir que as novas colunas existam na tabela 'empresas' (execução antes de qualquer query SQLAlchemy)
        colunas_empresas = [row[1] for row in db.session.execute(text("PRAGMA table_info(empresas)")).fetchall()]
        novas_colunas_empresas = {
            'is_admin': 'BOOLEAN DEFAULT 0',
            'status_assinatura': 'VARCHAR(20) DEFAULT "PENDENTE"',
            'data_validade': 'DATE',
            'observacoes_admin': 'TEXT DEFAULT ""',
            'chave_pix': 'VARCHAR(100) DEFAULT ""',
            'titular_pix': 'VARCHAR(100) DEFAULT "Fernando Cozac"',
            'valor_mensalidade': 'FLOAT DEFAULT 29.90',
            'valor_anual': 'FLOAT DEFAULT 249.90'
        }
        for col, col_type in novas_colunas_empresas.items():
            if col not in colunas_empresas:
                db.session.execute(text(f"ALTER TABLE empresas ADD COLUMN {col} {col_type}"))
        db.session.commit()

        # 2. Garantir que as colunas 'empresa_id' existam nas tabelas de dados
        tabelas = ['clientes', 'ordens_servico', 'transacoes']
        for tab in tabelas:
            colunas = [row[1] for row in db.session.execute(text(f"PRAGMA table_info({tab})")).fetchall()]
            if 'empresa_id' not in colunas:
                db.session.execute(text(f"ALTER TABLE {tab} ADD COLUMN empresa_id INTEGER REFERENCES empresas(id)"))
        db.session.commit()

        # 3. Criar a empresa padrão caso não exista nenhuma no banco
        empresa_padrao = Empresa.query.first()
        if not empresa_padrao:
            empresa_padrao = Empresa(
                nome_empresa="Valkar Peças e Serviços",
                subtitulo="Serviços Especializados e Atendimento Profissional",
                telefone="(62) 99494-1212",
                whatsapp="62994941212",
                endereco="",
                cidade_uf="",
                logo_filename="logo.png",
                mensagem_rodape="Agradecemos a preferência! Volte sempre.",
                email="nandocozac@gmail.com",
                senha_hash=generate_password_hash("Matheus10#"),
                is_admin=True,
                status_assinatura="ATIVO",
                chave_pix="nandocozac@gmail.com",
                titular_pix="Fernando Cozac",
                valor_mensalidade=29.90,
                valor_anual=249.90,
                data_validade=date(2099, 12, 31)
            )
            db.session.add(empresa_padrao)
            db.session.commit()
        else:
            # Garantir que a primeira empresa seja sempre Master Admin e Ativa
            if not empresa_padrao.is_admin or empresa_padrao.status_assinatura != 'ATIVO':
                empresa_padrao.is_admin = True
                empresa_padrao.status_assinatura = 'ATIVO'
                empresa_padrao.data_validade = date(2099, 12, 31)
                db.session.commit()

        # 4. Vincular dados anteriores existentes à empresa padrão
        db.session.execute(text(f"UPDATE clientes SET empresa_id = {empresa_padrao.id} WHERE empresa_id IS NULL"))
        db.session.execute(text(f"UPDATE ordens_servico SET empresa_id = {empresa_padrao.id} WHERE empresa_id IS NULL"))
        db.session.execute(text(f"UPDATE transacoes SET empresa_id = {empresa_padrao.id} WHERE empresa_id IS NULL"))
        db.session.commit()

migrar_banco_multiempresa()


# --- CONTROLE DE SESSÃO E IDENTIFICAÇÃO DO USUÁRIO ---

@app.before_request
def carregar_empresa_logada():
    empresa_id = session.get('empresa_id')
    if empresa_id:
        g.empresa = db.session.get(Empresa, empresa_id)
        if not g.empresa:
            session.pop('empresa_id', None)
            g.empresa = None
    else:
        g.empresa = None


@app.context_processor
def inject_empresa():
    return dict(empresa=getattr(g, 'empresa', None))


def login_required(view_func):
    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        if not getattr(g, 'empresa', None):
            flash('Por favor, faça login para acessar o sistema.', 'warning')
            return redirect(url_for('login', next=request.path))

        # Verificação de Bloqueio por Pagamento / Assinatura (exceto para o Dono Master)
        if not g.empresa.is_admin:
            hoje = date.today()
            esta_bloqueada = (g.empresa.status_assinatura != 'ATIVO') or (g.empresa.data_validade and g.empresa.data_validade < hoje)
            if esta_bloqueada:
                return redirect(url_for('assinatura_bloqueada'))

        return view_func(*args, **kwargs)
    return wrapped_view


def admin_required(view_func):
    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        if not getattr(g, 'empresa', None) or not g.empresa.is_admin:
            flash('Acesso restrito ao Administrador Master.', 'error')
            return redirect(url_for('dashboard'))
        return view_func(*args, **kwargs)
    return wrapped_view


# --- FILTROS DE FORMATAÇÃO (JINJA2) ---

@app.template_filter('moeda')
def formato_moeda(valor):
    try:
        val = float(valor or 0.0)
        return f"{val:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
    except (ValueError, TypeError):
        return "0,00"

@app.template_filter('data_br')
def formato_data(data_obj):
    if not data_obj:
        return "-"
    return data_obj.strftime('%d/%m/%Y')

def limpar_telefone_wa(telefone):
    if not telefone:
        return ""
    nums = re.sub(r'\D', '', str(telefone))
    if len(nums) in (10, 11):
        nums = '55' + nums
    return nums

@app.template_filter('fone_whatsapp')
def fone_whatsapp_filter(telefone):
    return limpar_telefone_wa(telefone)

@app.template_filter('msg_whatsapp_os')
def msg_whatsapp_os_filter(os_obj):
    cliente_nome = os_obj.cliente.nome.strip() if os_obj.cliente else "Cliente"
    item_info = f"{os_obj.veiculo.modelo} ({os_obj.veiculo.placa})" if os_obj.veiculo else "item / serviço"
    empresa = os_obj.empresa or getattr(g, 'empresa', None)
    nome_empresa = empresa.nome_empresa if empresa else "Empresa"
    
    linhas = [
        f"📋 *{nome_empresa.upper()}*",
        f"Olá, *{cliente_nome}*! 👋\n",
        f"Sua Ordem de Serviço *#{os_obj.id:04d}* foi finalizada e seu atendimento referente a *{item_info}* está concluído! ✅\n",
        "📋 *RESUMO DO ATENDIMENTO:*"
    ]
    
    pecas = [i for i in os_obj.itens if i.tipo == 'PECA']
    if pecas:
        linhas.append("\n📦 *Produtos / Peças / Materiais:*")
        for p in pecas:
            qtd_str = f"{p.quantidade:g}"
            preco_unit = formato_moeda(p.valor_unitario)
            sub_total = formato_moeda(p.subtotal)
            linhas.append(f"• {p.descricao} ({qtd_str}x R$ {preco_unit}) = R$ {sub_total}")
        linhas.append(f"  *Subtotal Materiais:* R$ {formato_moeda(os_obj.valor_pecas)}")
        
    servicos = [i for i in os_obj.itens if i.tipo == 'SERVICO']
    if servicos:
        linhas.append("\n🛠️ *Mão de Obra / Serviços Executados:*")
        for s in servicos:
            sub_total = formato_moeda(s.subtotal)
            linhas.append(f"• {s.descricao} = R$ {sub_total}")
        linhas.append(f"  *Subtotal Serviços:* R$ {formato_moeda(os_obj.valor_mao_obra)}")
        
    if os_obj.servico_executado:
        linhas.append(f"\n📝 *Observações / Laudo Técnico:*\n{os_obj.servico_executado}")
        
    linhas.append("\n" + ("─" * 22))
    linhas.append(f"💰 *VALOR TOTAL: R$ {formato_moeda(os_obj.valor_total)}*")
    if os_obj.forma_pagamento:
        linhas.append(f"💳 *Forma de Pagamento:* {os_obj.forma_pagamento}")
    if os_obj.retorno_previsto:
        linhas.append(f"📅 *Próxima Revisão Preventiva Sugerida:* {os_obj.retorno_previsto.strftime('%d/%m/%Y')}")
    linhas.append("🛡️ *Garantia:* 90 dias sobre serviços e materiais aplicados.")
    if empresa and empresa.endereco:
        loc = empresa.endereco
        if empresa.cidade_uf:
            loc += f" - {empresa.cidade_uf}"
        linhas.append(f"\n📍 *Endereço:* {loc}")
    if empresa and (empresa.telefone or empresa.whatsapp):
        linhas.append(f"📞 *Contato:* {empresa.whatsapp or empresa.telefone}")
    linhas.append("Aguardamos você para a retirada / entrega. Qualquer dúvida estamos à disposição! 🤝")
    
    return "\n".join(linhas)


# --- AUTENTICAÇÃO (LOGIN, CADASTRO, LOGOUT) ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    if getattr(g, 'empresa', None):
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        senha = request.form.get('senha', '').strip()

        empresa = Empresa.query.filter_by(email=email).first()
        if empresa and check_password_hash(empresa.senha_hash, senha):
            session['empresa_id'] = empresa.id
            flash(f"Bem-vindo(a), {empresa.nome_empresa}!", "success")
            next_url = request.args.get('next')
            return redirect(next_url or url_for('dashboard'))
        else:
            flash("E-mail ou senha incorretos. Tente novamente.", "error")

    return render_template('login.html')


@app.route('/cadastro', methods=['GET', 'POST'])
def cadastro():
    if getattr(g, 'empresa', None):
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        nome_empresa = request.form.get('nome_empresa', '').strip()
        email = request.form.get('email', '').strip().lower()
        telefone = request.form.get('telefone', '').strip()
        senha = request.form.get('senha', '').strip()
        confirmar_senha = request.form.get('confirmar_senha', '').strip()

        if not nome_empresa or not email or not senha:
            flash("Preencha todos os campos obrigatórios.", "error")
            return render_template('cadastro.html')

        if senha != confirmar_senha:
            flash("As senhas não coincidem.", "error")
            return render_template('cadastro.html')

        if len(senha) < 6:
            flash("A senha deve conter no mínimo 6 caracteres.", "error")
            return render_template('cadastro.html')

        if Empresa.query.filter_by(email=email).first():
            flash("Este e-mail já está cadastrado. Faça login ou use outro e-mail.", "error")
            return render_template('cadastro.html')

        # Nova empresa cadastrada fica PENDENTE aguardando aprovação/pagamento do Pix
        nova_empresa = Empresa(
            nome_empresa=nome_empresa,
            email=email,
            telefone=telefone,
            whatsapp=telefone,
            senha_hash=generate_password_hash(senha),
            is_admin=False,
            status_assinatura="PENDENTE",
            logo_filename="logo.png",
            mensagem_rodape="Agradecemos a preferência! Volte sempre."
        )
        db.session.add(nova_empresa)
        db.session.commit()

        session['empresa_id'] = nova_empresa.id
        flash(f"Conta da empresa '{nova_empresa.nome_empresa}' criada com sucesso! Siga as instruções abaixo para ativar seu acesso.", "info")
        return redirect(url_for('assinatura_bloqueada'))

    return render_template('cadastro.html')


@app.route('/logout')
def logout():
    session.pop('empresa_id', None)
    flash("Sua sessão foi encerrada com segurança.", "info")
    return redirect(url_for('login'))


# --- TELA DE BLOQUEIO POR FALTA DE PAGAMENTO ---

@app.route('/assinatura/bloqueada')
def assinatura_bloqueada():
    if not getattr(g, 'empresa', None):
        return redirect(url_for('login'))

    # Se for o Master Admin, nunca é bloqueado
    if g.empresa.is_admin:
        return redirect(url_for('dashboard'))

    hoje = date.today()
    esta_bloqueada = (g.empresa.status_assinatura != 'ATIVO') or (g.empresa.data_validade and g.empresa.data_validade < hoje)
    if not esta_bloqueada:
        return redirect(url_for('dashboard'))

    master_empresa = Empresa.query.filter_by(is_admin=True).first()
    return render_template('bloqueado.html', empresa=g.empresa, master_empresa=master_empresa)


# --- PAINEL DO ADMINISTRADOR MASTER (DONO DA PLATAFORMA) ---

@app.route('/admin/empresas')
@login_required
@admin_required
def admin_empresas():
    empresas = Empresa.query.order_by(Empresa.id.desc()).all()
    return render_template('admin_empresas.html', empresas=empresas, hoje=date.today())


@app.route('/admin/cobranca', methods=['POST'])
@login_required
@admin_required
def admin_salvar_cobranca():
    g.empresa.chave_pix = request.form.get('chave_pix', '').strip()
    g.empresa.titular_pix = request.form.get('titular_pix', '').strip()
    try:
        val_m = float(request.form.get('valor_mensalidade', '29.90').replace(',', '.'))
        g.empresa.valor_mensalidade = round(val_m, 2)
    except (ValueError, TypeError):
        pass

    try:
        val_a = float(request.form.get('valor_anual', '249.90').replace(',', '.'))
        g.empresa.valor_anual = round(val_a, 2)
    except (ValueError, TypeError):
        pass

    db.session.commit()
    flash("Dados de cobrança e Pix atualizados com sucesso!", "success")
    return redirect(url_for('admin_empresas'))


@app.route('/admin/empresa/<int:empresa_id>/renovar', methods=['POST'])
@login_required
@admin_required
def admin_renovar_empresa(empresa_id):
    emp = db.session.get(Empresa, empresa_id)
    if not emp:
        flash("Empresa não encontrada.", "error")
        return redirect(url_for('admin_empresas'))

    hoje = date.today()
    base_data = emp.data_validade if (emp.data_validade and emp.data_validade >= hoje) else hoje
    emp.data_validade = base_data + timedelta(days=30)
    emp.status_assinatura = 'ATIVO'
    db.session.commit()

    flash(f"Acesso da empresa '{emp.nome_empresa}' estendido por +30 dias (Válido até {emp.data_validade.strftime('%d/%m/%Y')})!", "success")
    return redirect(url_for('admin_empresas'))


@app.route('/admin/empresa/<int:empresa_id>/renovar-anual', methods=['POST'])
@login_required
@admin_required
def admin_renovar_empresa_anual(empresa_id):
    emp = db.session.get(Empresa, empresa_id)
    if not emp:
        flash("Empresa não encontrada.", "error")
        return redirect(url_for('admin_empresas'))

    hoje = date.today()
    base_data = emp.data_validade if (emp.data_validade and emp.data_validade >= hoje) else hoje
    emp.data_validade = base_data + timedelta(days=365)
    emp.status_assinatura = 'ATIVO'
    db.session.commit()

    flash(f"Acesso ANUAL da empresa '{emp.nome_empresa}' estendido por +1 ano (Válido até {emp.data_validade.strftime('%d/%m/%Y')})!", "success")
    return redirect(url_for('admin_empresas'))


@app.route('/admin/empresa/<int:empresa_id>/ativar', methods=['POST'])
@login_required
@admin_required
def admin_ativar_empresa(empresa_id):
    emp = db.session.get(Empresa, empresa_id)
    if not emp:
        flash("Empresa não encontrada.", "error")
        return redirect(url_for('admin_empresas'))

    hoje = date.today()
    emp.status_assinatura = 'ATIVO'
    if not emp.data_validade or emp.data_validade < hoje:
        emp.data_validade = hoje + timedelta(days=30)

    db.session.commit()
    flash(f"Acesso da empresa '{emp.nome_empresa}' liberado com sucesso!", "success")
    return redirect(url_for('admin_empresas'))


@app.route('/admin/empresa/<int:empresa_id>/bloquear', methods=['POST'])
@login_required
@admin_required
def admin_bloquear_empresa(empresa_id):
    emp = db.session.get(Empresa, empresa_id)
    if not emp:
        flash("Empresa não encontrada.", "error")
        return redirect(url_for('admin_empresas'))

    emp.status_assinatura = 'BLOQUEADO'
    db.session.commit()
    flash(f"Acesso da empresa '{emp.nome_empresa}' foi bloqueado.", "warning")
    return redirect(url_for('admin_empresas'))


@app.route('/admin/empresa/<int:empresa_id>/excluir', methods=['POST'])
@login_required
@admin_required
def admin_excluir_empresa(empresa_id):
    emp = db.session.get(Empresa, empresa_id)
    if not emp:
        flash("Empresa não encontrada.", "error")
        return redirect(url_for('admin_empresas'))

    if emp.is_admin or emp.id == g.empresa.id:
        flash("Não é permitido excluir a conta Master Admin principal!", "error")
        return redirect(url_for('admin_empresas'))

    nome = emp.nome_empresa

    # 1. Excluir transações financeiras da empresa
    Transacao.query.filter_by(empresa_id=emp.id).delete()

    # 2. Excluir ordens de serviço (e seus itens de OS associados)
    ordens = OrdemServico.query.filter_by(empresa_id=emp.id).all()
    for os_item in ordens:
        ItemOS.query.filter_by(os_id=os_item.id).delete()
        db.session.delete(os_item)

    # 3. Excluir veículos e clientes da empresa
    clientes = Cliente.query.filter_by(empresa_id=emp.id).all()
    for c in clientes:
        Veiculo.query.filter_by(cliente_id=c.id).delete()
        db.session.delete(c)

    # 4. Excluir a própria empresa
    db.session.delete(emp)
    db.session.commit()

    flash(f"A empresa '{nome}' e todos os seus registros foram excluídos com sucesso.", "success")
    return redirect(url_for('admin_empresas'))


# --- DASHBOARD ISOLADO POR EMPRESA ---

@app.route('/')
@login_required
def dashboard():
    hoje = date.today()
    empresa_id = g.empresa.id

    transacoes_hoje = Transacao.query.filter_by(empresa_id=empresa_id, data_movimento=hoje).all()
    receitas_hoje = sum(t.valor for t in transacoes_hoje if t.tipo == 'RECEITA')
    despesas_hoje = sum(t.valor for t in transacoes_hoje if t.tipo == 'DESPESA')
    saldo_hoje = receitas_hoje - despesas_hoje

    primeiro_dia_mes = hoje.replace(day=1)
    transacoes_mes = Transacao.query.filter(
        Transacao.empresa_id == empresa_id,
        Transacao.data_movimento >= primeiro_dia_mes
    ).all()
    receitas_mes = sum(t.valor for t in transacoes_mes if t.tipo == 'RECEITA')
    despesas_mes = sum(t.valor for t in transacoes_mes if t.tipo == 'DESPESA')
    faturamento_mes = receitas_mes - despesas_mes

    os_abertas = OrdemServico.query.filter_by(empresa_id=empresa_id, status='ABERTA').order_by(OrdemServico.id.desc()).all()

    return render_template(
        'dashboard.html',
        receitas_hoje=receitas_hoje,
        despesas_hoje=despesas_hoje,
        saldo_hoje=saldo_hoje,
        receitas_mes=receitas_mes,
        despesas_mes=despesas_mes,
        faturamento_mes=faturamento_mes,
        os_abertas=os_abertas
    )


# --- LANÇAMENTO RÁPIDO DE DESPESA ---

@app.route('/financeiro/despesa', methods=['POST'])
@login_required
def nova_despesa():
    descricao = request.form.get('descricao', '').strip()
    forma_pagamento = request.form.get('forma_pagamento', 'Dinheiro').strip()
    raw_valor = request.form.get('valor', '0').replace('.', '').replace(',', '.').strip()

    try:
        valor = float(raw_valor)
    except (ValueError, TypeError):
        valor = 0.0

    if descricao and valor > 0:
        nova = Transacao(
            empresa_id=g.empresa.id,
            tipo='DESPESA',
            descricao=descricao,
            valor=round(valor, 2),
            forma_pagamento=forma_pagamento,
            data_movimento=date.today()
        )
        db.session.add(nova)
        db.session.commit()

    return redirect(url_for('dashboard'))


# --- ORDENS DE SERVIÇO ---

@app.route('/os')
@login_required
def lista_os():
    status = request.args.get('status', '').strip()
    busca = request.args.get('busca', '').strip()

    query = OrdemServico.query.filter_by(empresa_id=g.empresa.id)

    if status:
        query = query.filter_by(status=status)

    if busca:
        query = query.join(Cliente).outerjoin(Veiculo).filter(
            (Cliente.nome.ilike(f'%{busca}%')) |
            (Cliente.telefone.ilike(f'%{busca}%')) |
            (Veiculo.placa.ilike(f'%{busca}%')) |
            (Veiculo.modelo.ilike(f'%{busca}%'))
        )

    todas_os = query.order_by(OrdemServico.id.desc()).all()
    return render_template('os_lista.html', todas_os=todas_os, status_atual=status, busca=busca)


@app.route('/os/nova', methods=['GET', 'POST'])
@login_required
def nova_os():
    if request.method == 'POST':
        nome = request.form.get('cliente_nome', '').strip()
        telefone = request.form.get('cliente_telefone', '').strip()
        placa = request.form.get('veiculo_placa', '').strip().upper()
        modelo = request.form.get('veiculo_modelo', '').strip()
        problema = request.form.get('problema', '').strip()

        # Busca ou cadastra cliente restrito a esta empresa
        cliente = Cliente.query.filter_by(empresa_id=g.empresa.id, telefone=telefone).first()
        if not cliente:
            cliente = Cliente(empresa_id=g.empresa.id, nome=nome, telefone=telefone)
            db.session.add(cliente)
            db.session.flush()

        veiculo = None
        if placa:
            veiculo = Veiculo.query.join(Cliente).filter(
                Cliente.empresa_id == g.empresa.id,
                Veiculo.placa == placa
            ).first()
            if not veiculo:
                veiculo = Veiculo(cliente_id=cliente.id, placa=placa, modelo=modelo)
                db.session.add(veiculo)
                db.session.flush()

        os_nova = OrdemServico(
            empresa_id=g.empresa.id,
            cliente_id=cliente.id,
            veiculo_id=veiculo.id if veiculo else None,
            descricao_problema=problema,
            status='ABERTA',
            data_abertura=date.today()
        )
        db.session.add(os_nova)
        db.session.commit()

        return redirect(url_for('ver_os', os_id=os_nova.id))

    return render_template('os_form.html')


@app.route('/os/<int:os_id>')
@login_required
def ver_os(os_id):
    os = OrdemServico.query.filter_by(id=os_id, empresa_id=g.empresa.id).first_or_404()
    return render_template('os_print.html', os=os)


@app.route('/os/<int:os_id>/pdf')
def ver_os_pdf(os_id):
    # O PDF pode ser acessado pelo cliente via WhatsApp ou impresso pela empresa
    os = OrdemServico.query.get_or_404(os_id)
    empresa = os.empresa
    return render_template('os_cliente_pdf.html', os=os, empresa=empresa)


@app.route('/os/<int:os_id>/concluir', methods=['POST'])
@login_required
def concluir_os(os_id):
    os = OrdemServico.query.filter_by(id=os_id, empresa_id=g.empresa.id).first_or_404()

    if os.status == 'CONCLUIDA':
        return redirect(url_for('ver_os', os_id=os.id))

    def converter_valor(v):
        try:
            return max(0.0, float(str(v or '0').replace('.', '').replace(',', '.').strip()))
        except (ValueError, TypeError):
            return 0.0

    try:
        # 1. Processar Peças / Materiais
        peca_nomes = request.form.getlist('peca_nome[]')
        peca_qtds = request.form.getlist('peca_qtd[]')
        peca_valores = request.form.getlist('peca_valor[]')

        total_pecas = 0.0
        for nome, qtd_str, val_str in zip(peca_nomes, peca_qtds, peca_valores):
            nome_clean = nome.strip()
            qtd = converter_valor(qtd_str) or 1.0
            val_unit = converter_valor(val_str)
            sub = round(qtd * val_unit, 2)
            if nome_clean:
                item = ItemOS(
                    os_id=os.id,
                    tipo='PECA',
                    descricao=nome_clean,
                    quantidade=qtd,
                    valor_unitario=val_unit,
                    subtotal=sub
                )
                db.session.add(item)
                total_pecas += sub

        # 2. Processar Serviços / Mão de Obra
        serv_nomes = request.form.getlist('servico_nome[]')
        serv_qtds = request.form.getlist('servico_qtd[]')
        serv_valores = request.form.getlist('servico_valor[]')

        total_servicos = 0.0
        for nome, qtd_str, val_str in zip(serv_nomes, serv_qtds, serv_valores):
            nome_clean = nome.strip()
            qtd = converter_valor(qtd_str) or 1.0
            val_unit = converter_valor(val_str)
            sub = round(qtd * val_unit, 2)
            if nome_clean:
                item = ItemOS(
                    os_id=os.id,
                    tipo='SERVICO',
                    descricao=nome_clean,
                    quantidade=qtd,
                    valor_unitario=val_unit,
                    subtotal=sub
                )
                db.session.add(item)
                total_servicos += sub

        # Suporte fallback caso os campos antigos de valor tenham sido preenchidos
        if total_pecas == 0:
            total_pecas = converter_valor(request.form.get('valor_pecas', '0'))
        if total_servicos == 0:
            total_servicos = converter_valor(request.form.get('valor_mao_obra', '0'))

        os.valor_pecas = round(total_pecas, 2)
        os.valor_mao_obra = round(total_servicos, 2)
        os.valor_total = round(total_pecas + total_servicos, 2)

        os.servico_executado = request.form.get('servico_executado', '').strip()
        os.forma_pagamento = request.form.get('forma_pagamento', 'Dinheiro').strip()
        os.status = 'CONCLUIDA'
        os.data_conclusao = date.today()
        os.retorno_previsto = date.today() + timedelta(days=90)

        # Entrada no Livro Caixa com isolamento de empresa
        if os.valor_total > 0:
            caixa = Transacao(
                empresa_id=g.empresa.id,
                tipo='RECEITA',
                descricao=f"Recebimento OS #{os.id} - {os.cliente.nome}",
                valor=os.valor_total,
                forma_pagamento=os.forma_pagamento,
                data_movimento=date.today(),
                os_id=os.id
            )
            db.session.add(caixa)

        db.session.commit()

    except Exception:
        db.session.rollback()
        raise

    return redirect(url_for('ver_os', os_id=os.id))


@app.route('/os/<int:os_id>/reabrir', methods=['POST'])
@login_required
def reabrir_os(os_id):
    os = OrdemServico.query.filter_by(id=os_id, empresa_id=g.empresa.id).first_or_404()

    if os.status == 'CONCLUIDA':
        # 1. Estorna a transação financeira do livro caixa da empresa
        transacao = Transacao.query.filter_by(os_id=os.id, empresa_id=g.empresa.id).first()
        if transacao:
            db.session.delete(transacao)

        # 2. Exclui os itens lançados para poder cadastrar a lista correta
        ItemOS.query.filter_by(os_id=os.id).delete()

        # 3. Retorna a OS para o estado ABERTA
        os.status = 'ABERTA'
        os.valor_pecas = 0.0
        os.valor_mao_obra = 0.0
        os.valor_total = 0.0
        os.forma_pagamento = None
        os.data_conclusao = None
        os.retorno_previsto = None

        db.session.commit()
        flash(f"A OS #{os.id:04d} foi reaberta para edição e o caixa foi estornado.", "success")

    return redirect(url_for('ver_os', os_id=os.id))


# --- FICHA DE CLIENTES E HISTÓRICO ISOLADO ---

@app.route('/clientes')
@login_required
def lista_clientes():
    busca = request.args.get('busca', '').strip()

    query = Cliente.query.filter_by(empresa_id=g.empresa.id)
    if busca:
        query = query.filter(
            (Cliente.nome.ilike(f'%{busca}%')) | 
            (Cliente.telefone.ilike(f'%{busca}%'))
        )

    clientes = query.order_by(Cliente.nome.asc()).all()
    return render_template('clientes.html', clientes=clientes, busca=busca)


@app.route('/clientes/<int:cliente_id>')
@login_required
def detalhe_cliente(cliente_id):
    cliente = Cliente.query.filter_by(id=cliente_id, empresa_id=g.empresa.id).first_or_404()

    historico_os = OrdemServico.query.filter_by(
        cliente_id=cliente.id,
        empresa_id=g.empresa.id
    ).order_by(OrdemServico.id.desc()).all()
    total_gasto = sum(os.valor_total for os in historico_os if os.status == 'CONCLUIDA')

    return render_template(
        'cliente_detalhe.html',
        cliente=cliente,
        historico_os=historico_os,
        total_gasto=total_gasto
    )


# --- EXTRATO FINANCEIRO ISOLADO ---

@app.route('/financeiro')
@login_required
def relatorio_financeiro():
    hoje = date.today()
    mes_selecionado = int(request.args.get('mes', hoje.month))
    ano_selecionado = int(request.args.get('ano', hoje.year))

    transacoes = Transacao.query.filter(
        Transacao.empresa_id == g.empresa.id,
        db.extract('month', Transacao.data_movimento) == mes_selecionado,
        db.extract('year', Transacao.data_movimento) == ano_selecionado
    ).order_by(Transacao.data_movimento.desc(), Transacao.id.desc()).all()

    total_receitas = sum(t.valor for t in transacoes if t.tipo == 'RECEITA')
    total_despesas = sum(t.valor for t in transacoes if t.tipo == 'DESPESA')
    resultado_liquido = total_receitas - total_despesas

    totais_pagamento = {}
    for t in transacoes:
        if t.tipo == 'RECEITA':
            fp = t.forma_pagamento or 'Não Informado'
            totais_pagamento[fp] = round(totais_pagamento.get(fp, 0.0) + t.valor, 2)

    return render_template(
        'financeiro.html',
        transacoes=transacoes,
        total_receitas=total_receitas,
        total_despesas=total_despesas,
        resultado_liquido=resultado_liquido,
        totais_pagamento=totais_pagamento,
        mes_atual=mes_selecionado,
        ano_atual=ano_selecionado
    )


@app.route('/financeiro/exportar')
@login_required
def exportar_financeiro():
    hoje = date.today()
    mes = int(request.args.get('mes', hoje.month))
    ano = int(request.args.get('ano', hoje.year))

    transacoes = Transacao.query.filter(
        Transacao.empresa_id == g.empresa.id,
        db.extract('month', Transacao.data_movimento) == mes,
        db.extract('year', Transacao.data_movimento) == ano
    ).order_by(Transacao.data_movimento.asc()).all()

    si = StringIO()
    writer = csv.writer(si, delimiter=';')
    writer.writerow(['Data', 'Tipo', 'Descricao', 'Forma Pagamento', 'Valor (R$)', 'OS Origem'])

    for t in transacoes:
        writer.writerow([
            t.data_movimento.strftime('%d/%m/%Y'),
            t.tipo,
            t.descricao,
            t.forma_pagamento or '-',
            f"{t.valor:.2f}".replace('.', ','),
            f"#{t.os_id:04d}" if t.os_id else 'Manual'
        ])

    output = Response(si.getvalue(), mimetype='text/csv')
    output.headers["Content-Disposition"] = f"attachment; filename=extrato_{mes:02d}_{ano}.csv"
    return output


# --- RETORNOS PREVENTIVOS ISOLADOS ---

@app.route('/retornos')
@login_required
def retornos_preventivos():
    hoje = date.today()
    janela_aviso = hoje + timedelta(days=15)

    alertas = OrdemServico.query.filter(
        OrdemServico.empresa_id == g.empresa.id,
        OrdemServico.retorno_previsto.between(hoje, janela_aviso),
        OrdemServico.status == 'CONCLUIDA'
    ).order_by(OrdemServico.retorno_previsto.asc()).all()

    return render_template('retornos.html', alertas=alertas)


# --- BACKUP DIRETO ---

@app.route('/sistema/backup')
@login_required
def backup_banco():
    caminho_db = os.path.join(app.root_path, 'instance', 'painel_gestao.db')
    if not os.path.exists(caminho_db):
        caminho_db = os.path.join(app.root_path, 'painel_gestao.db')

    data_str = date.today().strftime('%Y%m%d')
    return send_file(
        caminho_db,
        as_attachment=True,
        download_name=f"backup_painel_{data_str}.db"
    )


# --- CONFIGURAÇÕES DA EMPRESA (WHITE-LABEL) ---

@app.route('/configuracoes', methods=['GET', 'POST'])
@login_required
def configuracoes_empresa():
    empresa = g.empresa

    if request.method == 'POST':
        empresa.nome_empresa = request.form.get('nome_empresa', '').strip() or 'Minha Empresa'
        empresa.subtitulo = request.form.get('subtitulo', '').strip()
        empresa.cnpj_cpf = request.form.get('cnpj_cpf', '').strip()
        empresa.telefone = request.form.get('telefone', '').strip()
        empresa.whatsapp = request.form.get('whatsapp', '').strip()
        empresa.endereco = request.form.get('endereco', '').strip()
        empresa.cidade_uf = request.form.get('cidade_uf', '').strip()
        empresa.mensagem_rodape = request.form.get('mensagem_rodape', '').strip()

        # Atualização de e-mail e senha
        novo_email = request.form.get('email', '').strip().lower()
        if novo_email and novo_email != empresa.email:
            ja_existe = Empresa.query.filter_by(email=novo_email).first()
            if ja_existe:
                flash('Este e-mail já está sendo utilizado por outra empresa.', 'error')
            else:
                empresa.email = novo_email

        nova_senha = request.form.get('nova_senha', '').strip()
        if nova_senha:
            if len(nova_senha) < 6:
                flash('A nova senha deve ter no mínimo 6 dígitos.', 'error')
            else:
                empresa.senha_hash = generate_password_hash(nova_senha)
                flash('Senha atualizada com sucesso!', 'info')

        if 'logo' in request.files:
            file = request.files['logo']
            if file and file.filename != '':
                ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
                if ext in ['png', 'jpg', 'jpeg', 'webp', 'svg']:
                    nome_arquivo = f"logo_{empresa.id}_{int(time.time())}.{ext}"
                    caminho_salvar = os.path.join(app.config['UPLOAD_FOLDER'], nome_arquivo)
                    file.save(caminho_salvar)
                    empresa.logo_filename = f"uploads/{nome_arquivo}"
                else:
                    flash('Formato de logo inválido! Envie imagem PNG, JPG, WEBP ou SVG.', 'error')

        db.session.commit()
        flash('Dados da empresa atualizados com sucesso!', 'success')
        return redirect(url_for('configuracoes_empresa'))

    return render_template('configuracoes.html', empresa=empresa)


@app.route('/configuracoes/remover-logo', methods=['POST'])
@login_required
def remover_logo_empresa():
    empresa = g.empresa
    empresa.logo_filename = 'logo.png'
    db.session.commit()
    flash('Logotipo redefinido para o padrão.', 'info')
    return redirect(url_for('configuracoes_empresa'))


if __name__ == '__main__':
    app.run(debug=True, port=5000)