from datetime import date, datetime, timezone
from database import db

class Empresa(db.Model):
    __tablename__ = 'empresas'
    
    id = db.Column(db.Integer, primary_key=True)
    nome_empresa = db.Column(db.String(150), nullable=False, default="Minha Empresa")
    subtitulo = db.Column(db.String(200), default="Serviços Especializados e Atendimento Profissional")
    cnpj_cpf = db.Column(db.String(30), default="")
    telefone = db.Column(db.String(30), default="")
    whatsapp = db.Column(db.String(30), default="")
    endereco = db.Column(db.String(200), default="")
    cidade_uf = db.Column(db.String(100), default="")
    logo_filename = db.Column(db.String(255), nullable=True, default=None)
    mensagem_rodape = db.Column(db.String(255), default="Agradecemos a preferência! Volte sempre.")
    
    # Credenciais de Acesso
    email = db.Column(db.String(120), unique=True, nullable=False)
    senha_hash = db.Column(db.String(255), nullable=False)
    data_criacao = db.Column(db.Date, default=date.today)
    
    # Segmento / Ramo de Atividade ('OFICINA' ou 'LOJA')
    tipo_negocio = db.Column(db.String(30), default='OFICINA')

    # Controle de Acesso / Assinaturas (SaaS)
    is_admin = db.Column(db.Boolean, default=False)
    status_assinatura = db.Column(db.String(20), default='PENDENTE') # 'ATIVO', 'PENDENTE', 'BLOQUEADO'
    data_validade = db.Column(db.Date, nullable=True)
    observacoes_admin = db.Column(db.Text, default="")
    
    # Dados de cobrança (definidos pelo Dono Master para receber dos clientes)
    chave_pix = db.Column(db.String(100), default="nandocozac@gmail.com")
    titular_pix = db.Column(db.String(100), default="Fernando Cozac")
    valor_mensalidade = db.Column(db.Float, default=29.90)
    valor_anual = db.Column(db.Float, default=249.90)
    valor_mensalidade_fiscal = db.Column(db.Float, default=79.90)
    valor_anual_fiscal = db.Column(db.Float, default=699.90)

    # Controle do Plano Fiscal SaaS (Dono Master habilita para quem paga)
    permite_emissao_fiscal = db.Column(db.Boolean, default=False)

    # Configurações Fiscais da Empresa (Focus NFe)
    fiscal_ativo = db.Column(db.Boolean, default=False)
    fiscal_ambiente = db.Column(db.String(20), default='HOMOLOGACAO') # 'HOMOLOGACAO' ou 'PRODUCAO'
    fiscal_cnpj = db.Column(db.String(20), default='')
    fiscal_razao_social = db.Column(db.String(150), default='')
    fiscal_nome_fantasia = db.Column(db.String(150), default='')
    fiscal_inscricao_municipal = db.Column(db.String(30), default='')
    fiscal_inscricao_estadual = db.Column(db.String(30), default='')
    fiscal_codigo_municipio = db.Column(db.String(10), default='5208707') # Código IBGE (ex: 5208707 Goiânia)
    fiscal_regime_tributario = db.Column(db.Integer, default=1) # 1=Simples Nacional, 2=Simples Excesso, 3=Normal, 4=MEI
    fiscal_certificado_filename = db.Column(db.String(255), nullable=True)
    fiscal_certificado_senha = db.Column(db.String(255), default='')
    fiscal_token_focus = db.Column(db.String(100), default='')

    # Relacionamentos isolados por empresa
    clientes = db.relationship('Cliente', backref='empresa', lazy=True)
    ordens_servico = db.relationship('OrdemServico', backref='empresa', lazy=True)
    transacoes = db.relationship('Transacao', backref='empresa', lazy=True)


class Cliente(db.Model):
    __tablename__ = 'clientes'
    
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=True)
    nome = db.Column(db.String(120), nullable=False)
    telefone = db.Column(db.String(30), nullable=False)
    
    veiculos = db.relationship('Veiculo', backref='cliente', lazy=True)
    ordens_servico = db.relationship('OrdemServico', backref='cliente', lazy=True)


class Veiculo(db.Model):
    __tablename__ = 'veiculos'
    
    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey('clientes.id'), nullable=False)
    placa = db.Column(db.String(20), nullable=False)
    modelo = db.Column(db.String(100), nullable=False)
    
    ordens_servico = db.relationship('OrdemServico', backref='veiculo', lazy=True)


class OrdemServico(db.Model):
    __tablename__ = 'ordens_servico'
    
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey('clientes.id'), nullable=False)
    veiculo_id = db.Column(db.Integer, db.ForeignKey('veiculos.id'), nullable=True)
    
    descricao_problema = db.Column(db.Text, nullable=False)
    servico_executado = db.Column(db.Text, nullable=True)
    
    valor_pecas = db.Column(db.Float, default=0.0)
    valor_mao_obra = db.Column(db.Float, default=0.0)
    valor_total = db.Column(db.Float, default=0.0)
    forma_pagamento = db.Column(db.String(30), nullable=True)
    
    status = db.Column(db.String(20), default='ABERTA') # 'ABERTA' ou 'CONCLUIDA'
    data_abertura = db.Column(db.Date, default=date.today)
    data_conclusao = db.Column(db.Date, nullable=True)
    retorno_previsto = db.Column(db.Date, nullable=True)

    itens = db.relationship('ItemOS', backref='ordem_servico', cascade="all, delete-orphan", lazy=True)


class ItemOS(db.Model):
    __tablename__ = 'itens_os'
    
    id = db.Column(db.Integer, primary_key=True)
    os_id = db.Column(db.Integer, db.ForeignKey('ordens_servico.id'), nullable=False)
    tipo = db.Column(db.String(10), nullable=False) # 'PECA' ou 'SERVICO'
    descricao = db.Column(db.String(150), nullable=False)
    quantidade = db.Column(db.Float, default=1.0)
    valor_unitario = db.Column(db.Float, default=0.0)
    subtotal = db.Column(db.Float, default=0.0)
    
    # Dados tributários opcionais para emissão de nota
    ncm = db.Column(db.String(10), default="") # NCM de 8 dígitos para peças/produtos
    cfop = db.Column(db.String(10), default="") # Ex: 5102, 5405
    codigo_servico_municipal = db.Column(db.String(20), default="") # Ex: 14.01
    aliquota_iss = db.Column(db.Float, default=0.0)


class Transacao(db.Model):
    __tablename__ = 'transacoes'
    
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=True)
    tipo = db.Column(db.String(10), nullable=False) # 'RECEITA' ou 'DESPESA'
    descricao = db.Column(db.String(200), nullable=False)
    valor = db.Column(db.Float, nullable=False)
    forma_pagamento = db.Column(db.String(30), nullable=True)
    data_movimento = db.Column(db.Date, default=date.today)
    
    os_id = db.Column(db.Integer, db.ForeignKey('ordens_servico.id'), nullable=True)


class NotaFiscal(db.Model):
    __tablename__ = 'notas_fiscais'

    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=False)
    ordem_servico_id = db.Column(db.Integer, db.ForeignKey('ordens_servico.id'), nullable=True)

    tipo_nota = db.Column(db.String(10), nullable=False) # 'NFSE', 'NFE', 'NFCE'
    referencia_uuid = db.Column(db.String(64), unique=True, nullable=False)
    numero_nota = db.Column(db.String(30), default="")
    serie_nota = db.Column(db.String(10), default="")
    chave_acesso = db.Column(db.String(60), default="")
    status = db.Column(db.String(30), default='PROCESSANDO') # 'PROCESSANDO', 'AUTORIZADA', 'CANCELADA', 'ERRO'
    mensagem_sefaz = db.Column(db.Text, default="")
    url_danfe_pdf = db.Column(db.String(255), default="")
    url_xml = db.Column(db.String(255), default="")
    valor_total = db.Column(db.Float, default=0.0)
    data_emissao = db.Column(db.Date, default=date.today)

    empresa = db.relationship('Empresa', backref=db.backref('notas_fiscais', lazy=True))
    ordem_servico = db.relationship('OrdemServico', backref=db.backref('notas_fiscais', lazy=True))