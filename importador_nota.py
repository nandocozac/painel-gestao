import re
import xml.etree.ElementTree as ET
from io import BytesIO

def extrair_dados_xml(conteudo_bytes):
    """
    Processa arquivo XML de NF-e (Modelo 55 ou 65) padrão SEFAZ nacional.
    Retorna metadados da nota e lista discriminada de produtos/itens.
    """
    try:
        root = ET.fromstring(conteudo_bytes)
    except Exception as e:
        return {'sucesso': False, 'erro': f'Arquivo XML inválido ou corrompido: {str(e)}'}

    # Remover namespaces para facilitar busca de tags (ex: {http://www.portalfiscal.inf.br/nfe})
    for elem in root.iter():
        if '}' in elem.tag:
            elem.tag = elem.tag.split('}', 1)[1]

    # Metadados
    ide = root.find('.//ide')
    emit = root.find('.//emit')
    infNFe = root.find('.//infNFe')
    chNFe = root.find('.//chNFe')

    chave_acesso = ''
    if infNFe is not None and 'Id' in infNFe.attrib:
        chave_acesso = re.sub(r'\D', '', infNFe.attrib['Id'])
    elif chNFe is not None and chNFe.text:
        chave_acesso = re.sub(r'\D', '', chNFe.text)

    numero_nota = ''
    data_emissao = ''
    if ide is not None:
        nNF = ide.find('nNF')
        if nNF is not None and nNF.text:
            numero_nota = nNF.text.strip()
        dhEmi = ide.find('dhEmi') or ide.find('dEmi')
        if dhEmi is not None and dhEmi.text:
            data_emissao = dhEmi.text[:10]

    fornecedor_nome = ''
    fornecedor_cnpj = ''
    if emit is not None:
        xNome = emit.find('xNome')
        if xNome is not None and xNome.text:
            fornecedor_nome = xNome.text.strip()
        cnpj = emit.find('CNPJ') or emit.find('CPF')
        if cnpj is not None and cnpj.text:
            fornecedor_cnpj = cnpj.text.strip()

    itens = []
    dets = root.findall('.//det')
    for det in dets:
        prod = det.find('prod')
        if prod is None:
            continue

        cProd = prod.find('cProd')
        xProd = prod.find('xProd')
        ncm = prod.find('NCM')
        cfop = prod.find('CFOP')
        uCom = prod.find('uCom')
        qCom = prod.find('qCom')
        vUnCom = prod.find('vUnCom')
        vProd = prod.find('vProd')

        codigo = cProd.text.strip() if cProd is not None and cProd.text else ''
        nome = xProd.text.strip() if xProd is not None and xProd.text else 'Produto sem descrição'
        ncm_val = ncm.text.strip() if ncm is not None and ncm.text else ''
        cfop_val = cfop.text.strip() if cfop is not None and cfop.text else ''
        unidade = (uCom.text.strip().upper() if uCom is not None and uCom.text else 'UN')[:10]

        try:
            qtd = float(qCom.text) if qCom is not None and qCom.text else 1.0
        except (ValueError, TypeError):
            qtd = 1.0

        try:
            v_unit = float(vUnCom.text) if vUnCom is not None and vUnCom.text else 0.0
        except (ValueError, TypeError):
            v_unit = 0.0

        try:
            v_total = float(vProd.text) if vProd is not None and vProd.text else (qtd * v_unit)
        except (ValueError, TypeError):
            v_total = qtd * v_unit

        itens.append({
            'codigo': codigo,
            'nome': nome,
            'ncm': ncm_val,
            'cfop': cfop_val,
            'unidade': unidade,
            'quantidade': round(qtd, 2),
            'preco_custo': round(v_unit, 2),
            'subtotal': round(v_total, 2)
        })

    if not itens:
        return {'sucesso': False, 'erro': 'Nenhum produto/item encontrado na estrutura do XML.'}

    return {
        'sucesso': True,
        'tipo_arquivo': 'XML',
        'chave_acesso': chave_acesso,
        'numero_nota': numero_nota,
        'fornecedor_nome': fornecedor_nome,
        'fornecedor_cnpj': fornecedor_cnpj,
        'data_emissao': data_emissao,
        'itens': itens
    }


def extrair_dados_pdf(conteudo_bytes):
    """
    Processa arquivo PDF da DANFE (Documento Auxiliar da Nota Fiscal Eletrônica).
    Extrai texto das páginas, localiza chave de acesso de 44 dígitos e tabela de produtos.
    """
    try:
        from pypdf import PdfReader
        reader = PdfReader(BytesIO(conteudo_bytes))
        texto_completo = ""
        for page in reader.pages:
            t = page.extract_text()
            if t:
                texto_completo += t + "\n"
    except Exception as e:
        return {'sucesso': False, 'erro': f'Não foi possível ler o arquivo PDF: {str(e)}'}

    if not texto_completo.strip():
        return {'sucesso': False, 'erro': 'O PDF fornecido parece ser uma imagem digitalizada sem camada de texto pesquisável.'}

    # 1. Chave de acesso de 44 dígitos
    chave_acesso = ''
    match_chave = re.search(r'\b(?:\d{4}\s*){11}\b', texto_completo)
    if match_chave:
        chave_acesso = re.sub(r'\D', '', match_chave.group(0))
    else:
        # Fallback para sequência contínua de 44 números
        match_cont = re.search(r'\b\d{44}\b', texto_completo)
        if match_cont:
            chave_acesso = match_cont.group(0)

    # 2. Número da Nota
    numero_nota = ''
    match_num = re.search(r'(?:N[º°\.]|N[uú]mero\s*:?)\s*([0-9\.\,]+)', texto_completo, re.IGNORECASE)
    if match_num:
        numero_nota = re.sub(r'\D', '', match_num.group(1))

    # 3. Fornecedor / Razão Social
    fornecedor_nome = ''
    linhas = [l.strip() for l in texto_completo.split('\n') if l.strip()]
    if linhas:
        # Primeira ou segunda linha geralmente é o nome da empresa emitente
        for l in linhas[:5]:
            if 'RECEBEMOS DE' in l.upper():
                partes = l.upper().split('RECEBEMOS DE')
                if len(partes) > 1 and partes[1].strip():
                    fornecedor_nome = partes[1].strip().split(' OS PRODUTOS')[0]
                    break
        if not fornecedor_nome:
            for l in linhas[:3]:
                if len(l) > 4 and not re.search(r'DANFE|DOCUMENTO|AUXILIAR|NF-E', l, re.IGNORECASE):
                    fornecedor_nome = l
                    break

    # 4. CNPJ Fornecedor
    fornecedor_cnpj = ''
    match_cnpj = re.search(r'\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b', texto_completo)
    if match_cnpj:
        fornecedor_cnpj = match_cnpj.group(0)

    # 5. Extração de itens / produtos
    # Padrão típico de linha de produto DANFE:
    # Código | Descrição | NCM | CST | CFOP | UN | QTD | VL UNIT | VL TOTAL
    itens = []
    
    # Regex flexível para linhas de produto:
    # Exemplo: 001 PASTILHA FREIO DIANT 87083090 0102 5102 UN 2,00 90,00 180,00
    padrao_produto = re.compile(
        r'(?P<cod>[\w\-\.]{2,20})\s+'
        r'(?P<nome>.+?)\s+'
        r'(?P<ncm>\d{8}|\d{2}\.\d{3}\.\d{2})?\s*'
        r'(?:\d{3,4}\s+)?' # CST/CSOSN opcional
        r'(?P<cfop>\d{4})?\s+'
        r'(?P<un>[A-Z]{2,4})\s+'
        r'(?P<qtd>\d+[\.,]\d+|\d+)\s+'
        r'(?P<vunit>\d+[\.,]\d{2,4})\s+'
        r'(?P<vtot>\d+[\.,]\d{2})'
    )

    def converter_num(v_str):
        if not v_str:
            return 0.0
        s = str(v_str).strip()
        if ',' in s and '.' in s:
            if s.rfind(',') > s.rfind('.'):
                s = s.replace('.', '').replace(',', '.')
            else:
                s = s.replace(',', '')
        elif ',' in s:
            s = s.replace(',', '.')
        try:
            return float(s)
        except ValueError:
            return 0.0

    for l in linhas:
        m = padrao_produto.search(l)
        if m:
            nome_prod = m.group('nome').strip()
            # Ignorar cabeçalhos falsos positivos
            if any(palavra in nome_prod.upper() for palavra in ['DESCRIÇÃO', 'PRODUTO', 'VALOR', 'QUANTIDADE', 'BASE DE CÁLCULO']):
                continue

            qtd = converter_num(m.group('qtd'))
            vunit = converter_num(m.group('vunit'))
            vtot = converter_num(m.group('vtot'))

            itens.append({
                'codigo': m.group('cod').strip(),
                'nome': nome_prod,
                'ncm': re.sub(r'\D', '', m.group('ncm') or ''),
                'cfop': m.group('cfop') or '',
                'unidade': m.group('un').strip().upper(),
                'quantidade': round(qtd, 2) if qtd > 0 else 1.0,
                'preco_custo': round(vunit, 2),
                'subtotal': round(vtot, 2)
            })

    # Se a regex estrita não pegou linhas tabeladas, fazemos uma busca mais tolerante por linhas de itens
    if not itens:
        padrao_tolerante = re.compile(r'(.+?)\s+([A-Z]{2})\s+(\d+[\.,]\d+)\s+(\d+[\.,]\d{2})\s+(\d+[\.,]\d{2})')
        for l in linhas:
            m = padrao_tolerante.search(l)
            if m:
                desc = m.group(1).strip()
                if not any(k in desc.upper() for k in ['DESCRIÇÃO', 'BASE', 'CÁLCULO', 'TOTAL', 'EMISSÃO', 'PROTOCOLO']):
                    qtd = converter_num(m.group(3))
                    vunit = converter_num(m.group(4))
                    vtot = converter_num(m.group(5))
                    itens.append({
                        'codigo': '',
                        'nome': desc,
                        'ncm': '',
                        'cfop': '',
                        'unidade': m.group(2).upper(),
                        'quantidade': round(qtd, 2) if qtd > 0 else 1.0,
                        'preco_custo': round(vunit, 2),
                        'subtotal': round(vtot, 2)
                    })

    if not itens:
        return {
            'sucesso': False, 
            'erro': 'Conseguimos ler a nota, mas o formato de tabela de itens deste PDF é muito específico ou contém campos compactados. Recomendamos utilizar o arquivo XML da NF-e fornecido pelo vendedor para 100% de precisão.'
        }

    return {
        'sucesso': True,
        'tipo_arquivo': 'PDF',
        'chave_acesso': chave_acesso,
        'numero_nota': numero_nota,
        'fornecedor_nome': fornecedor_nome,
        'fornecedor_cnpj': fornecedor_cnpj,
        'data_emissao': '',
        'itens': itens
    }


def processar_arquivo_nota(arquivo_storage):
    """
    Identifica se o arquivo enviado é XML ou PDF e chama o extrator adequado.
    """
    nome_arquivo = arquivo_storage.filename.lower()
    conteudo = arquivo_storage.read()

    if not conteudo:
        return {'sucesso': False, 'erro': 'O arquivo enviado está vazio.'}

    # Detecção por cabeçalho binário ou extensão
    if nome_arquivo.endswith('.xml') or conteudo.startswith(b'<?xml') or b'<nfeProc' in conteudo[:200] or b'<NFe' in conteudo[:200]:
        return extrair_dados_xml(conteudo)
    elif nome_arquivo.endswith('.pdf') or conteudo.startswith(b'%PDF'):
        return extrair_dados_pdf(conteudo)
    else:
        return {'sucesso': False, 'erro': 'Formato não suportado. Por favor, envie um arquivo .XML ou .PDF da nota fiscal.'}
