import base64
import json
import re
import xml.etree.ElementTree as ET
from io import BytesIO
import requests

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


def extrair_dados_ia(conteudo_bytes, nome_arquivo="documento.pdf", api_key=None):
    """
    Processa documentos digitalizados (PDFs sem camada de texto, fotos de notas fiscais,
    cupons e recibos em JPG/PNG/WEBP) utilizando a API multimodal do Google Gemini.
    """
    if not api_key or not str(api_key).strip():
        return {
            'sucesso': False,
            'erro': (
                'Este documento é uma imagem ou PDF escaneado (sem texto digital pesquisável). '
                'Para processá-lo com Inteligência Artificial, informe sua chave gratuita do Google Gemini em '
                '"Configurações da Empresa" ou utilize o arquivo XML da NF-e fornecido pelo fornecedor.'
            )
        }

    # Detectar MIME type
    nome_low = (nome_arquivo or '').lower()
    if nome_low.endswith('.pdf') or conteudo_bytes.startswith(b'%PDF'):
        mime_type = 'application/pdf'
        tipo_doc_desc = 'PDF Escaneado (IA)'
    elif nome_low.endswith(('.jpg', '.jpeg')) or conteudo_bytes.startswith(b'\xff\xd8'):
        mime_type = 'image/jpeg'
        tipo_doc_desc = 'Foto / Imagem (IA)'
    elif nome_low.endswith('.png') or conteudo_bytes.startswith(b'\x89PNG'):
        mime_type = 'image/png'
        tipo_doc_desc = 'Imagem PNG (IA)'
    elif nome_low.endswith('.webp'):
        mime_type = 'image/webp'
        tipo_doc_desc = 'Imagem WEBP (IA)'
    else:
        mime_type = 'application/pdf' if b'%PDF' in conteudo_bytes[:50] else 'image/jpeg'
        tipo_doc_desc = 'Documento Escaneado (IA)'

    b64_data = base64.b64encode(conteudo_bytes).decode('utf-8')

    prompt = (
        "Você é um especialista em documentos fiscais e comerciais brasileiros (DANFE, NF-e, NFC-e, "
        "Cupom Fiscal, Pedido de Compra e Faturas). Analise cuidadosamente o documento em anexo.\n\n"
        "Extraia todos os itens e produtos discriminados na nota, bem como os metadados do fornecedor e da nota.\n\n"
        "Retorne EXCLUSIVAMENTE um objeto JSON válido seguindo estritamente a estrutura abaixo, "
        "sem nenhuma marcação ou texto explicativo fora do JSON:\n"
        "{\n"
        '  "chave_acesso": "chave de acesso com 44 digitos se visivel ou string vazia",\n'
        '  "numero_nota": "numero da nota fiscal ou pedido",\n'
        '  "fornecedor_nome": "razao social ou nome fantasia do emitente/fornecedor",\n'
        '  "fornecedor_cnpj": "CNPJ ou CPF do emitente se presente",\n'
        '  "data_emissao": "AAAA-MM-DD se identificavel ou string vazia",\n'
        '  "itens": [\n'
        "    {\n"
        '      "codigo": "codigo ou referencia do item se houver",\n'
        '      "nome": "descricao completa do produto ou mercadoria",\n'
        '      "ncm": "codigo NCM se visivel",\n'
        '      "cfop": "CFOP se visivel",\n'
        '      "unidade": "UN, PC, CX, KG, LT, MT etc",\n'
        '      "quantidade": 1.0,\n'
        '      "preco_custo": 0.0,\n'
        '      "subtotal": 0.0\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "Importante: se houver múltiplos itens ou produtos, liste todos eles no array 'itens'. "
        "Converta quantidades e valores monetários para números decimais com ponto (float)."
    )

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {
                        "inlineData": {
                            "mimeType": mime_type,
                            "data": b64_data
                        }
                    }
                ]
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.1
        }
    }

    # Modelos suportados (ordem de tentativa: gemini-2.0-flash com fallback para gemini-1.5-flash)
    modelos = ["gemini-2.0-flash", "gemini-1.5-flash"]
    ultimo_erro = None

    for modelo in modelos:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent?key={str(api_key).strip()}"
        try:
            resp = requests.post(
                url,
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=45
            )
            if resp.status_code == 200:
                resp_json = resp.json()
                candidates = resp_json.get('candidates', [])
                if not candidates:
                    return {'sucesso': False, 'erro': 'A IA não conseguiu interpretar o conteúdo deste documento.'}

                texto_resposta = candidates[0].get('content', {}).get('parts', [{}])[0].get('text', '').strip()
                if texto_resposta.startswith("```json"):
                    texto_resposta = texto_resposta[7:]
                elif texto_resposta.startswith("```"):
                    texto_resposta = texto_resposta[3:]
                if texto_resposta.endswith("```"):
                    texto_resposta = texto_resposta[:-3]
                texto_resposta = texto_resposta.strip()

                try:
                    dados = json.loads(texto_resposta)
                except Exception as ex_json:
                    return {'sucesso': False, 'erro': f'Erro ao ler dados da IA: {str(ex_json)}'}

                raw_itens = dados.get('itens', [])
                itens_processados = []
                for it in raw_itens:
                    nome_item = str(it.get('nome') or '').strip()
                    if not nome_item:
                        continue

                    try:
                        qtd = float(it.get('quantidade') or 1.0)
                    except (ValueError, TypeError):
                        qtd = 1.0

                    try:
                        custo = float(it.get('preco_custo') or 0.0)
                    except (ValueError, TypeError):
                        custo = 0.0

                    try:
                        subtotal = float(it.get('subtotal') or (qtd * custo))
                    except (ValueError, TypeError):
                        subtotal = qtd * custo

                    un = str(it.get('unidade') or 'UN').strip().upper()[:10]
                    cod = str(it.get('codigo') or '').strip()
                    ncm = re.sub(r'\D', '', str(it.get('ncm') or ''))
                    cfop = re.sub(r'\D', '', str(it.get('cfop') or ''))

                    itens_processados.append({
                        'codigo': cod,
                        'nome': nome_item,
                        'ncm': ncm,
                        'cfop': cfop,
                        'unidade': un or 'UN',
                        'quantidade': round(qtd, 2) if qtd > 0 else 1.0,
                        'preco_custo': round(custo, 2),
                        'subtotal': round(subtotal, 2)
                    })

                if not itens_processados:
                    return {
                        'sucesso': False,
                        'erro': 'A IA analisou a imagem/documento, mas não localizou itens legíveis de produtos.'
                    }

                chave = re.sub(r'\D', '', str(dados.get('chave_acesso') or ''))
                if len(chave) != 44:
                    chave = ''

                return {
                    'sucesso': True,
                    'tipo_arquivo': tipo_doc_desc,
                    'chave_acesso': chave,
                    'numero_nota': str(dados.get('numero_nota') or '').strip(),
                    'fornecedor_nome': str(dados.get('fornecedor_nome') or '').strip(),
                    'fornecedor_cnpj': str(dados.get('fornecedor_cnpj') or '').strip(),
                    'data_emissao': str(dados.get('data_emissao') or '').strip(),
                    'itens': itens_processados
                }
            elif resp.status_code in [400, 403]:
                err_data = resp.json().get('error', {})
                msg_err = err_data.get('message', 'Chave ou permissão inválida')
                return {
                    'sucesso': False,
                    'erro': f'Falha na validação do Google Gemini: {msg_err}. Verifique sua chave em Configurações.'
                }
            elif resp.status_code == 429:
                return {
                    'sucesso': False,
                    'erro': 'Limite temporário da cota gratuita da API Gemini atingido. Aguarde cerca de 1 minuto.'
                }
            else:
                ultimo_erro = f"HTTP {resp.status_code}: {resp.text[:120]}"
        except requests.exceptions.Timeout:
            ultimo_erro = "Tempo limite de resposta do Google Gemini esgotado."
        except Exception as e:
            ultimo_erro = str(e)

    return {
        'sucesso': False,
        'erro': f'Não foi possível processar a imagem/PDF com IA: {ultimo_erro or "Falha de comunicação."}'
    }


def extrair_dados_pdf(conteudo_bytes, api_key=None):
    """
    Processa arquivo PDF da DANFE (Documento Auxiliar da Nota Fiscal Eletrônica).
    Extrai texto das páginas, localiza chave de acesso de 44 dígitos e tabela de produtos.
    Caso seja um PDF escaneado (sem texto), delega para a IA do Google Gemini.
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
        if api_key:
            return extrair_dados_ia(conteudo_bytes, 'documento_escaneado.pdf', api_key=api_key)
        return {'sucesso': False, 'erro': f'Não foi possível ler o arquivo PDF: {str(e)}'}

    # Se não houver texto extraível, o PDF é uma digitalização/imagem
    if not texto_completo.strip():
        if api_key:
            return extrair_dados_ia(conteudo_bytes, 'documento_escaneado.pdf', api_key=api_key)
        return {
            'sucesso': False,
            'erro': (
                'O PDF fornecido parece ser uma imagem digitalizada sem camada de texto pesquisável. '
                'Para ler documentos escaneados e fotos com Inteligência Artificial, informe sua chave '
                'gratuita do Google Gemini em "Configurações da Empresa" ou utilize o arquivo XML da NF-e.'
            )
        }

    # 1. Chave de acesso de 44 dígitos
    chave_acesso = ''
    match_chave = re.search(r'\b(?:\d{4}\s*){11}\b', texto_completo)
    if match_chave:
        chave_acesso = re.sub(r'\D', '', match_chave.group(0))
    else:
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
    itens = []
    padrao_produto = re.compile(
        r'(?P<cod>[\w\-\.]{2,20})\s+'
        r'(?P<nome>.+?)\s+'
        r'(?P<ncm>\d{8}|\d{2}\.\d{3}\.\d{2})?\s*'
        r'(?:\d{3,4}\s+)?'
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
        # Tentar leitura via IA como fallback se houver chave
        if api_key:
            res_ia = extrair_dados_ia(conteudo_bytes, 'documento_layout_complexo.pdf', api_key=api_key)
            if res_ia.get('sucesso'):
                return res_ia

        return {
            'sucesso': False, 
            'erro': 'Conseguimos abrir a nota, mas o formato de tabela de itens deste PDF é muito específico ou contém campos compactados. Recomendamos utilizar o arquivo XML da NF-e fornecido pelo vendedor para 100% de precisão.'
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


def processar_arquivo_nota(arquivo_storage, api_key=None):
    """
    Identifica o formato do arquivo enviado (XML, PDF nativo, PDF escaneado ou Imagem)
    e chama o extrator adequado, com suporte a IA multimodal para imagens e digitalizações.
    """
    nome_arquivo = (arquivo_storage.filename or '').lower()
    conteudo = arquivo_storage.read()

    if not conteudo:
        return {'sucesso': False, 'erro': 'O arquivo enviado está vazio.'}

    # Detecção por cabeçalho binário ou extensão
    if nome_arquivo.endswith('.xml') or conteudo.startswith(b'<?xml') or b'<nfeProc' in conteudo[:200] or b'<NFe' in conteudo[:200]:
        return extrair_dados_xml(conteudo)
    elif nome_arquivo.endswith('.pdf') or conteudo.startswith(b'%PDF'):
        return extrair_dados_pdf(conteudo, api_key=api_key)
    elif nome_arquivo.endswith(('.jpg', '.jpeg', '.png', '.webp')) or conteudo.startswith(b'\xff\xd8') or conteudo.startswith(b'\x89PNG'):
        return extrair_dados_ia(conteudo, nome_arquivo, api_key=api_key)
    else:
        return {
            'sucesso': False, 
            'erro': 'Formato não suportado. Por favor, envie um arquivo .XML (NF-e), .PDF (DANFE) ou imagem da nota (.JPG, .PNG).'
        }
