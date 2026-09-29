import streamlit as st

def check_password():
    # Auto-login via token no URL (vindo da landing page Premium Farma)
    params = st.query_params
    if params.get("token") == st.secrets["my_password"]:
        st.session_state["password_correct"] = True

    if "password_correct" not in st.session_state:
        st.text_input("Introduza a palavra-passe", type="password", key="password")
        if st.button("Entrar"):
            if st.session_state["password"] == st.secrets["my_password"]:
                st.session_state["password_correct"] = True
                st.rerun()
            else:
                st.error("Palavra-passe incorreta")
        return False
    return True

if not check_password():
    st.stop()
import pandas as pd
import altair as alt
import io
from PIL import Image

# Configuração da página
icon = Image.open('PremiumFarma-appIcon-iOS.png')
st.set_page_config(
    page_title="Sugestão de Rentabilidade Máxima", 
    page_icon=icon, 
    layout="wide",
    initial_sidebar_state="collapsed"
)

st.markdown("""
    <style>
    .main { background-color: #f5f7f9; }
    .stMetric { background-color: #ffffff; padding: 15px; border-radius: 10px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
    .stExpander { border: 1px solid #e0e0e0; border-radius: 10px; background-color: #ffffff; margin-bottom: 20px; }
    </style>
    """, unsafe_allow_html=True)

# Cabeçalho
col_logo, col_title = st.columns([1, 12])
with col_logo:
    st.image(icon, width=70)
with col_title:
    st.title("Sugestão de Rentabilidade Máxima")

def clean_code(val):
    if pd.isna(val): return None
    s = str(val).strip()
    if s.endswith('.0'): s = s[:-2]
    return s

def load_data(file):
    name = file.name.lower()
    if name.endswith('.csv'):
        # Lê tudo como texto: a conversão numérica é feita depois,
        # para suportar vírgula decimal (Softreis) e ponto decimal (Sifarma)
        raw = file.read() if hasattr(file, 'read') else open(file, 'rb').read()
        for enc in ('utf-8-sig', 'cp1252', 'latin-1'):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        return pd.read_csv(io.StringIO(text), sep=';', dtype=str, on_bad_lines='skip', low_memory=False)
    elif name.endswith('.xls'):
        return pd.read_excel(file, engine='xlrd')
    else:
        return pd.read_excel(file)

def to_num(series):
    """Converte para número aceitando '8,61', '8.61' e '1.234,56'."""
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors='coerce')
    s = series.astype(str).str.strip()
    has_comma = s.str.contains(',', regex=False)
    s = s.where(~has_comma, s.str.replace('.', '', regex=False).str.replace(',', '.', regex=False))
    return pd.to_numeric(s, errors='coerce')

def detect_origem(vendas):
    cols = set(vendas.columns)
    if 'V01' in cols and 'V13' in cols:
        return 'Softreis'
    if 'V0' in cols and all(f'V{i}' in cols for i in range(1, 13)):
        return 'Sifarma'
    return 'Outro'

def normalize_vendas(vendas):
    """Converte os ficheiros de vendas dos vários softwares numa estrutura comum."""
    vendas = vendas.copy()
    vendas.columns = [str(c).strip() for c in vendas.columns]
    origem = detect_origem(vendas)

    # Vendas dos últimos 12 meses completos (sem o mês corrente)
    #  - Sifarma:  V0 = mês corrente, V1..V12 = 12 meses anteriores
    #  - Softreis: V01 = mês corrente, V02..V13 = 12 meses anteriores
    if origem == 'Sifarma':
        v_cols = [f'V{i}' for i in range(1, 13)]
    elif origem == 'Softreis':
        v_cols = [f'V{i:02d}' for i in range(2, 14)]
    else:
        v_cols = [f'V{i}' for i in range(1, 13)]
        if not all(c in vendas.columns for c in v_cols):
            v_cols = ['V13'] if 'V13' in vendas.columns else []
    for c in v_cols:
        vendas[c] = to_num(vendas[c]).fillna(0)
    vendas['V13_TOTAL'] = vendas[v_cols].sum(axis=1) if v_cols else 0

    for c in ['PVP', 'PCU', 'IVA']:
        if c in vendas.columns:
            vendas[c] = to_num(vendas[c])

    if 'NOM' in vendas.columns:
        vendas['NOM'] = vendas['NOM'].astype(str).str.strip().str.strip('"').str.strip()

    # Campos que a Softreis exporta vazios (GEN, LABAIM) ficam como NaN
    for c in ['GEN', 'LABAIM']:
        if c in vendas.columns:
            vendas[c] = vendas[c].astype(str).str.strip().replace({'': None, 'nan': None, 'None': None})
    return vendas, origem

def find_col(df, *patterns):
    for p in patterns:
        for c in df.columns:
            if p in str(c).lower():
                return c
    return None

def process_files(cardex_file, vendas_file, db_file, pvp_margin):
    try:
        cardex = load_data(cardex_file)
        vendas, origem = normalize_vendas(load_data(vendas_file))
        db = load_data(db_file) if db_file else None
        diag = {'origem': origem, 'linhas': len(vendas)}

        # 1. Identificar CNP nas Vendas
        v_cnp_col = 'CPR' if 'CPR' in vendas.columns else ('CNP' if 'CNP' in vendas.columns else None)
        if v_cnp_col:
            vendas['CNP_MATCH'] = vendas[v_cnp_col].apply(clean_code)
        else:
            return "Erro: Coluna CNP/CPR não encontrada nas Vendas."

        # 2. Lookup da Base de Dados
        prices_lookup = None
        status_map = None
        if db is not None:
            db_cnp_col = 'Nº registo' if 'Nº registo' in db.columns else None
            if db_cnp_col:
                db['CNP_MATCH'] = db[db_cnp_col].apply(clean_code)
                price_cols = [c for c in ['Preço Utente', 'Preço Pensionistas'] if c in db.columns]
                prices_lookup = db.dropna(subset=['CNP_MATCH']).drop_duplicates('CNP_MATCH').set_index('CNP_MATCH')[price_cols]
                if 'Comerc.' in db.columns:
                    status_map = db.dropna(subset=['CNP_MATCH']).drop_duplicates('CNP_MATCH').set_index('CNP_MATCH')['Comerc.']
                db_idx = db.dropna(subset=['CNP_MATCH']).drop_duplicates('CNP_MATCH').set_index('CNP_MATCH')
                if 'CNPEM' not in vendas.columns:
                    vendas['CNPEM'] = vendas['CNP_MATCH'].map(db_idx['CNPEM'].dropna())
                # Softreis não exporta GEN nem LABAIM: completar a partir da Base de Dados
                db_gen_col = find_col(db, 'genérico', 'generico')
                if db_gen_col and ('GEN' not in vendas.columns or vendas['GEN'].isna().any()):
                    gen_db = db_idx[db_gen_col].astype(str).str.strip().str.upper().str[0].map({'S': 'S', 'N': 'N', 'T': 'S', 'F': 'N'})
                    vendas['GEN'] = vendas.get('GEN', pd.Series(index=vendas.index, dtype=object)).fillna(vendas['CNP_MATCH'].map(gen_db))
                db_tit_col = find_col(db, 'titular')
                if db_tit_col and ('LABAIM' not in vendas.columns or vendas['LABAIM'].isna().any()):
                    vendas['LABAIM'] = vendas.get('LABAIM', pd.Series(index=vendas.index, dtype=object)).fillna(vendas['CNP_MATCH'].map(db_idx[db_tit_col]))

        # 3. Rentabilidade Vendas (V13_TOTAL já calculado em normalize_vendas)
        if 'PVP' not in vendas.columns or 'PCU' not in vendas.columns:
            return "Erro: o ficheiro de Vendas não tem as colunas PVP e/ou PCU."
        vendas['PVP_Vendas'] = vendas['PVP']
        vendas['IVA'] = vendas['IVA'].fillna(6) if 'IVA' in vendas.columns else 6
        vendas['Rentabilidade_Vendas'] = (vendas['PVP_Vendas'] / (1 + vendas['IVA']/100)) - vendas['PCU']
        diag['com_pvp'] = int((vendas['PVP_Vendas'] > 0).sum())

        if 'CNPEM' not in vendas.columns or vendas['CNPEM'].isna().all():
            return f"Erro: CNPEM não encontrado para nenhum produto das Vendas ({origem}). Verifique a Base de Dados."
        diag['com_cnpem'] = int(vendas['CNPEM'].notna().sum())

        # 4. Preparação Cardex (Suporte para Cardex Total)
        cardex['CNPEM_MATCH'] = cardex['CNPEM'].apply(clean_code)
        cardex['CNP_STR'] = cardex['CNP'].apply(clean_code)
        
        c_pvp_col = 'PVP (R)' if 'PVP (R)' in cardex.columns else 'PVP'
        c_rent_col = 'Rentb. Máxima' if 'Rentb. Máxima' in cardex.columns else 'Rentabilidade'
        c_name_col = 'Descrição' if 'Descrição' in cardex.columns else 'Nome do produto'

        if 'Margem' in cardex.columns and 'Margem.1' in cardex.columns and 'Margem.4' in cardex.columns:
            def identify_typology(row):
                if row['Margem'] == row[c_rent_col]: return "AT"
                if row['Margem.1'] == row[c_rent_col]: return "TFO"
                if row['Margem.4'] == row[c_rent_col]: return "Delegado"
                return "Outro"
            cardex['Tipologia'] = cardex.apply(identify_typology, axis=1)
        else:
            cardex['Tipologia'] = "N/A"

        if status_map is not None:
            cardex['Status_Comerc'] = cardex['CNP_STR'].map(status_map)
            cardex = cardex[~cardex['Status_Comerc'].str.contains('Não comercializado|Esgotado', case=False, na=False)]

        cardex_clean = cardex[['CNPEM_MATCH', c_pvp_col, c_rent_col, c_name_col, 'Laboratório', 'CNP', 'Tipologia']].copy()
        cardex_clean.columns = ['CNPEM_MATCH', 'PVP_Cardex', 'Rentabilidade_Cardex', 'Nome_Cardex', 'Lab_Cardex', 'CNP_Cardex', 'Tipologia']
        cardex_clean['PVP_Cardex'] = pd.to_numeric(cardex_clean['PVP_Cardex'], errors='coerce')
        cardex_clean['Rentabilidade_Cardex'] = pd.to_numeric(cardex_clean['Rentabilidade_Cardex'], errors='coerce')

        vendas['CNPEM_MATCH'] = vendas['CNPEM'].apply(clean_code)
        # Se ainda faltar GEN: um produto que está no Cardex (lista de genéricos) é genérico
        if 'GEN' not in vendas.columns:
            vendas['GEN'] = None
        cardex_cnps = set(cardex['CNP_STR'].dropna())
        vendas['GEN'] = vendas['GEN'].fillna(vendas['CNP_MATCH'].map(lambda c: 'S' if c in cardex_cnps else 'N'))
        v_lab_col = 'LABAIM' if 'LABAIM' in vendas.columns else ('Laboratório' if 'Laboratório' in vendas.columns else None)
        vendas = vendas.rename(columns={v_lab_col: 'Lab_Vendas'}) if v_lab_col else vendas.assign(Lab_Vendas="Desconhecido")
        # Softreis não traz laboratório: usar o do Cardex quando o produto lá existe
        lab_cardex = cardex.dropna(subset=['CNP_STR']).drop_duplicates('CNP_STR').set_index('CNP_STR')['Laboratório']
        vendas['Lab_Vendas'] = vendas['Lab_Vendas'].fillna(vendas['CNP_MATCH'].map(lab_cardex)).fillna("Desconhecido")

        # 5. Match Final
        vendas['_ROW'] = range(len(vendas))
        merged = pd.merge(vendas, cardex_clean, on='CNPEM_MATCH')
        diag['match_cnpem'] = int(merged['_ROW'].nunique()) if not merged.empty else 0
        merged = merged[abs(merged['PVP_Cardex'] - merged['PVP_Vendas']) <= pvp_margin]
        diag['match_pvp'] = int(merged['_ROW'].nunique()) if not merged.empty else 0

        if merged.empty: return diag

        # Uma única sugestão (a mais rentável) por produto vendido
        idx = merged.groupby('_ROW')['Rentabilidade_Cardex'].idxmax()
        results = merged.loc[idx].copy()

        if prices_lookup is not None:
            results = results.join(prices_lookup, on='CNP_MATCH', rsuffix='_Atual')
            results['CNP_Cardex_STR'] = results['CNP_Cardex'].apply(clean_code)
            results = results.join(prices_lookup, on='CNP_Cardex_STR', rsuffix='_Sugerido')

        results['Diff_Rent'] = results['Rentabilidade_Cardex'] - results['Rentabilidade_Vendas']
        results = results[results['Diff_Rent'] > 0.01].copy()
        results['Ganho_Potencial'] = results['Diff_Rent'] * results['V13_TOTAL']
        if results.empty: return diag

        results = results.sort_values(by='Ganho_Potencial', ascending=False)
        results.attrs['diag'] = diag
        return results
    except Exception as e:
        return f"Erro técnico: {e}"

# --- INTERFACE ---
with st.expander("📁 Gestão de Ficheiros", expanded=True):
    c1, c2, c3 = st.columns(3)
    cardex_upload = c1.file_uploader("1. Cardex (2026 ou Total)", type=['xlsx'])
    vendas_upload = c2.file_uploader("2. Vendas (.xlsx ou .csv)", type=['xlsx', 'csv'])
    db_upload = c3.file_uploader("3. Base de Dados (allPackages.xls)", type=['xls'])

if cardex_upload and vendas_upload and db_upload:
    st.subheader("🎯 Configuração da Estratégia")
    f1, f2, f3, f4 = st.columns([2, 2, 1.5, 1])
    escolha_gen = f3.selectbox("Substituir a partir de:", options=["Ambos", "Genérico", "Marca"])
    pvp_margin = f4.number_input("Margem PVP (€):", min_value=0.0, value=0.0, step=0.10)

    results_raw = process_files(cardex_upload, vendas_upload, db_upload, pvp_margin)
    
    if isinstance(results_raw, str):
        st.error(results_raw)
    elif isinstance(results_raw, dict):
        d = results_raw
        st.warning(
            f"Nenhuma oportunidade encontrada. Ficheiro de vendas: **{d.get('origem')}** · "
            f"{d.get('linhas', 0)} produtos · {d.get('com_pvp', 0)} com PVP · "
            f"{d.get('com_cnpem', 0)} com CNPEM · {d.get('match_cnpem', 0)} com alternativas no Cardex · "
            f"{d.get('match_pvp', 0)} dentro da margem de PVP"
        )
    elif results_raw is not None and not results_raw.empty:
        st.caption(f"Ficheiro de vendas detetado: **{results_raw.attrs.get('diag', {}).get('origem', '?')}**")
        all_v_labs = sorted(results_raw['Lab_Vendas'].dropna().unique())
        selected_v_lab = f1.selectbox("Trocar do Lab (Vendas):", options=["Todos"] + all_v_labs)
        all_c_labs = sorted(results_raw['Lab_Cardex'].dropna().unique())
        selected_c_lab = f2.selectbox("Substituir pelo Lab (Cardex):", options=["Todos"] + all_c_labs)

        filtered = results_raw.copy()
        if escolha_gen != "Ambos":
            filtered = filtered[filtered['GEN'] == {"Genérico": "S", "Marca": "N"}[escolha_gen]]
        if selected_v_lab != "Todos": filtered = filtered[filtered['Lab_Vendas'] == selected_v_lab]
        if selected_c_lab != "Todos": filtered = filtered[filtered['Lab_Cardex'] == selected_c_lab]
        
        if not filtered.empty:
            st.divider()
            c1, c2, c3 = st.columns(3)
            c1.metric("GANHO ESTIMADO", f"{filtered['Ganho_Potencial'].sum():,.2f} €")
            c2.metric("UNIDADES (12M)", f"{int(filtered['V13_TOTAL'].sum())} un")
            c3.metric("SUBSTITUIÇÕES", len(filtered))
            
            st.divider()
            st.subheader("📋 Detalhe Comparativo")
            
            cols_to_show = [
                'CNP_MATCH', 'NOM', 'PVP_Vendas', 'Preço Utente', 'Preço Pensionistas',
                'CNP_Cardex', 'Nome_Cardex', 'PVP_Cardex', 'Preço Utente_Sugerido', 'Preço Pensionistas_Sugerido',
                'Rentabilidade_Vendas', 'Rentabilidade_Cardex', 'V13_TOTAL', 'Ganho_Potencial', 'Tipologia'
            ]
            display_df = filtered[cols_to_show].copy()
            display_df.columns = [
                'CNP Atual', 'Produto Atual', 'PVP Atual', 'Utente Atual', 'Pens. Atual',
                'CNP Sugerido', 'Substituir por', 'PVP Sugerido', 'Utente Novo', 'Pens. Novo',
                'Rent. Atual', 'Rent. Nova', 'Vendas', 'Ganho Est.', 'Tipologia'
            ]
            
            for col in ['CNP Atual', 'CNP Sugerido']: display_df[col] = display_df[col].astype(str).str.split('.').str[0]

            st.dataframe(display_df.style.format({
                'PVP Atual': '{:.2f} €', 'PVP Sugerido': '{:.2f} €',
                'Utente Atual': '{:.2f} €', 'Utente Novo': '{:.2f} €',
                'Pens. Atual': '{:.2f} €', 'Pens. Novo': '{:.2f} €',
                'Rent. Atual': '{:.2f}', 'Rent. Nova': '{:.2f}', 
                'Vendas': '{:.0f}', 'Ganho Est.': '{:.2f} €'
            }), use_container_width=True, height=600, hide_index=True)
            
            st.download_button("📥 Exportar Relatório", display_df.to_csv(index=False).encode('utf-8'), "premiumfarma_tipologia.csv", "text/csv")
        else:
            st.warning("Sem resultados para os filtros selecionados.")
    else:
        st.warning("Nenhuma oportunidade encontrada.")
else:
    st.info("💡 Carregue os 3 ficheiros para análise. Suporta o novo 'Cardex Total' com Tipologia.")
