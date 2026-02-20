"""
RDC Allocation & Route Optimizer — Web App
===========================================
Run: streamlit run app.py
"""

import streamlit as st
import pandas as pd
import io, time

from optimizer import (
    load_allocation, load_bin_stock, load_store_master, run_optimizer
)

st.set_page_config(
    page_title="RDC Optimizer",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ─── CUSTOM CSS ───
st.markdown("""
<style>
    .stApp { background-color: #0a0f1a; }
    section[data-testid="stSidebar"] { background-color: #111827; }
    .metric-card {
        background: #1a2236; border: 1px solid #2a3552; border-radius: 12px;
        padding: 16px 20px; text-align: center;
    }
    .metric-label { color: #8896b3; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 1px; }
    .metric-value { color: #e2e8f0; font-size: 28px; font-weight: 700; font-family: monospace; }
    .metric-sub { color: #4b5c7a; font-size: 11px; margin-top: 2px; }
    .green { color: #10b981; } .red { color: #ef4444; } .amber { color: #f59e0b; } .blue { color: #3b82f6; }
    div[data-testid="stDataFrame"] { border: 1px solid #2a3552; border-radius: 10px; }
</style>
""", unsafe_allow_html=True)


def metric_card(label, value, sub="", color="#3b82f6"):
    return f"""
    <div class="metric-card" style="border-top: 3px solid {color};">
        <div class="metric-label">{label}</div>
        <div class="metric-value">{value}</div>
        <div class="metric-sub">{sub}</div>
    </div>"""


def to_csv_bytes(df):
    buf = io.BytesIO()
    df.to_csv(buf, index=False)
    return buf.getvalue()


# ═══════════════════════════════════════════════════════
#  SIDEBAR — UPLOAD & CONFIG
# ═══════════════════════════════════════════════════════

with st.sidebar:
    st.markdown("### 📦 RDC Optimizer")
    st.markdown("---")
    
    st.markdown("##### 📁 Upload Files")
    alloc_file = st.file_uploader("Allocation (XLSB/XLSX)", type=['xlsb', 'xlsx', 'xls'], key='alloc')
    stock_file = st.file_uploader("Bin Stock (XLSB/XLSX)", type=['xlsb', 'xlsx', 'xls'], key='stock')
    master_file = st.file_uploader("Store Master (XLSX)", type=['xlsx', 'xls'], key='master')
    
    st.markdown("---")
    st.markdown("##### ⚙️ Settings")
    
    force_dc = st.selectbox("Force DC", ["Auto (Store Master)", "DH24", "DW01"], index=1)
    
    col1, col2 = st.columns(2)
    with col1:
        target_qty = st.number_input("Target Qty/Picker", value=400, min_value=100, step=50)
    with col2:
        soft_cap = st.number_input("Soft Cap", value=600, min_value=200, step=50)
    
    exclude_empty = st.checkbox("Exclude 'Empty' status bins", value=True)
    candidate_bins = st.slider("Candidate bins per SKU", 10, 100, 50)
    
    st.markdown("---")
    
    all_uploaded = alloc_file and stock_file and master_file
    run_btn = st.button(
        "🚀 Run Optimizer",
        type="primary",
        use_container_width=True,
        disabled=not all_uploaded
    )
    
    if not all_uploaded:
        st.caption("⬆️ Upload all 3 files to enable")


# ═══════════════════════════════════════════════════════
#  MAIN AREA
# ═══════════════════════════════════════════════════════

if 'results' not in st.session_state:
    st.session_state.results = None

# ─── RUN OPTIMIZER ───
if run_btn and all_uploaded:
    st.session_state.results = None
    
    config = {
        'target_qty': target_qty,
        'soft_cap': soft_cap,
        'force_dc': None if force_dc.startswith("Auto") else force_dc,
        'exclude_bin_types': [],
        'exclude_statuses': ['Empty'] if exclude_empty else [],
        'candidate_bins': candidate_bins,
    }
    
    progress_bar = st.progress(0, text="Loading files...")
    log_area = st.empty()
    running_logs = []
    
    def progress_cb(pct=None, msg=None):
        if msg:
            running_logs.append(msg)
            log_area.code("\n".join(running_logs[-12:]), language="text")
        if pct is not None:
            progress_bar.progress(min(pct, 100) / 100, text=f"Processing... {pct}%")
    
    try:
        progress_cb(pct=5, msg="Loading allocation file...")
        demand_df = load_allocation(alloc_file)
        progress_cb(pct=10, msg=f"  → {len(demand_df):,} demand lines, {demand_df['store_id'].nunique()} stores")
        
        progress_cb(pct=15, msg="Loading bin stock file...")
        stock_df, sheet_info = load_bin_stock(stock_file)
        for sn, cnt in sheet_info.items():
            progress_cb(msg=f"  → {sn}: {cnt:,} bins")
        progress_cb(pct=25, msg=f"  → Total: {len(stock_df):,} stocked bins")
        
        progress_cb(pct=30, msg="Loading store master...")
        store_master = load_store_master(master_file)
        progress_cb(pct=35, msg=f"  → {len(store_master)} stores mapped")
        
        picklist, shortages, kpis, logs = run_optimizer(
            demand_df, stock_df, store_master, config, progress_cb=progress_cb
        )
        
        st.session_state.results = {
            'picklist': picklist,
            'shortages': shortages,
            'kpis': kpis,
            'logs': logs,
            'demand_df': demand_df,
            'config': config,
        }
        
        progress_bar.progress(1.0, text="✅ Complete!")
        time.sleep(0.5)
        progress_bar.empty()
        log_area.empty()
        st.rerun()
        
    except Exception as e:
        progress_bar.empty()
        st.error(f"❌ Error: {str(e)}")
        st.exception(e)


# ─── SHOW RESULTS ───
if st.session_state.results:
    r = st.session_state.results
    picklist = r['picklist']
    shortages = r['shortages']
    kpis = r['kpis']
    demand_df = r['demand_df']
    
    ta = int(picklist['pick_qty'].sum()) if len(picklist) > 0 else 0
    td = int(demand_df['demand_qty'].sum())
    fp = round(ta / td * 100, 1) if td > 0 else 0
    ns = len(shortages[shortages['reason'] == 'no_stock']) if len(shortages) > 0 and 'reason' in shortages.columns else 0
    ins = len(shortages[shortages['reason'] == 'insufficient_stock']) if len(shortages) > 0 and 'reason' in shortages.columns else 0
    tot_pickers = int(kpis['pickers_required'].sum()) if len(kpis) > 0 else 0
    
    # ─── KPI CARDS ───
    cols = st.columns(7)
    cards = [
        ("Pick Lines", f"{len(picklist):,}", "", "#3b82f6"),
        ("Allocated", f"{ta:,}", f"of {td:,}", "#60a5fa"),
        ("Fulfillment", f"{fp}%", "", "#10b981" if fp >= 95 else "#f59e0b"),
        ("Shortages", f"{len(shortages):,}", f"{ns} no stock • {ins} insufficient", "#ef4444"),
        ("Bins Used", f"{picklist['bin_id'].nunique():,}" if len(picklist) > 0 else "0", "", "#f59e0b"),
        ("Stores", f"{kpis[kpis['total_allocated']>0]['store_id'].nunique() if len(kpis)>0 else 0}/{len(kpis)}", "", "#8b5cf6"),
        ("Pickers", f"{tot_pickers:,}", "", "#10b981"),
    ]
    for i, (label, val, sub, color) in enumerate(cards):
        with cols[i]:
            st.markdown(metric_card(label, val, sub, color), unsafe_allow_html=True)
    
    st.markdown("<br>", unsafe_allow_html=True)
    
    # ─── TABS ───
    tab1, tab2, tab3, tab4, tab5 = st.tabs(["📋 Picklist", "⚠️ Shortages", "📊 Store KPIs", "📈 Analysis", "📝 Log"])
    
    # ─── PICKLIST TAB ───
    with tab1:
        c1, c2 = st.columns([3, 1])
        with c1:
            st.markdown(f"**{len(picklist):,}** pick lines • Route-optimized • Floor-isolated pickers")
        with c2:
            st.download_button("⬇️ Download Full CSV", to_csv_bytes(picklist), "picklist.csv", "text/csv", use_container_width=True)
        
        # Filters
        fc1, fc2, fc3 = st.columns(3)
        with fc1:
            stores = ["All"] + sorted(picklist['store_id'].unique().tolist()) if len(picklist) > 0 else ["All"]
            sel_store = st.selectbox("Filter Store", stores, key='pl_store')
        with fc2:
            floors = ["All"] + sorted(picklist['floor'].unique().tolist()) if len(picklist) > 0 else ["All"]
            sel_floor = st.selectbox("Filter Floor", floors, key='pl_floor')
        with fc3:
            pickers = ["All"] + sorted(picklist['picker_no'].unique().tolist()) if len(picklist) > 0 else ["All"]
            sel_picker = st.selectbox("Filter Picker", pickers, key='pl_picker')
        
        display = picklist.copy()
        if sel_store != "All":
            display = display[display['store_id'] == sel_store]
        if sel_floor != "All":
            display = display[display['floor'] == sel_floor]
        if sel_picker != "All":
            display = display[display['picker_no'] == sel_picker]
        
        st.dataframe(display.head(500), use_container_width=True, height=500)
        if len(display) > 500:
            st.caption(f"Showing 500 of {len(display):,} rows. Download CSV for full data.")
    
    # ─── SHORTAGES TAB ───
    with tab2:
        c1, c2 = st.columns([3, 1])
        with c1:
            st.markdown(f"**{len(shortages):,}** shortages • **{ns}** no stock • **{ins}** insufficient")
        with c2:
            if len(shortages) > 0:
                st.download_button("⬇️ Download CSV", to_csv_bytes(shortages), "shortages.csv", "text/csv", use_container_width=True)
        
        if len(shortages) > 0:
            # Summary by reason
            rc1, rc2 = st.columns(2)
            with rc1:
                reason_summary = shortages.groupby('reason').agg(
                    count=('short_qty', 'count'),
                    total_short=('short_qty', 'sum')
                ).reset_index()
                st.dataframe(reason_summary, use_container_width=True, hide_index=True)
            with rc2:
                top_short_stores = shortages.groupby(['store_id', 'store_name']).agg(
                    shortage_lines=('short_qty', 'count'),
                    total_short_qty=('short_qty', 'sum')
                ).sort_values('total_short_qty', ascending=False).head(10).reset_index()
                st.markdown("**Top 10 stores by shortage qty**")
                st.dataframe(top_short_stores, use_container_width=True, hide_index=True)
            
            st.dataframe(shortages.head(500), use_container_width=True, height=400)
    
    # ─── KPIs TAB ───
    with tab3:
        c1, c2 = st.columns([3, 1])
        with c1:
            st.markdown(f"**{len(kpis)}** stores")
        with c2:
            if len(kpis) > 0:
                st.download_button("⬇️ Download CSV", to_csv_bytes(kpis), "kpis.csv", "text/csv", use_container_width=True)
        
        if len(kpis) > 0:
            st.dataframe(
                kpis.sort_values('fulfillment_pct', ascending=False),
                use_container_width=True,
                height=500,
                hide_index=True
            )
    
    # ─── ANALYSIS TAB ───
    with tab4:
        if len(kpis) > 0:
            # Fulfillment distribution
            st.markdown("#### Fulfillment Distribution")
            bands = [
                ("100%+", len(kpis[kpis['fulfillment_pct'] >= 100]), "#10b981"),
                ("95-99%", len(kpis[(kpis['fulfillment_pct'] >= 95) & (kpis['fulfillment_pct'] < 100)]), "#34d399"),
                ("80-94%", len(kpis[(kpis['fulfillment_pct'] >= 80) & (kpis['fulfillment_pct'] < 95)]), "#f59e0b"),
                ("50-79%", len(kpis[(kpis['fulfillment_pct'] >= 50) & (kpis['fulfillment_pct'] < 80)]), "#f97316"),
                ("<50%", len(kpis[(kpis['fulfillment_pct'] > 0) & (kpis['fulfillment_pct'] < 50)]), "#ef4444"),
                ("0%", len(kpis[kpis['fulfillment_pct'] == 0]), "#6b7280"),
            ]
            band_df = pd.DataFrame(bands, columns=['Range', 'Stores', 'Color'])
            
            for _, brow in band_df.iterrows():
                pct = brow['Stores'] / len(kpis) * 100 if len(kpis) > 0 else 0
                st.markdown(
                    f"""<div style="display:flex;align-items:center;gap:10px;margin-bottom:6px">
                    <span style="width:60px;text-align:right;font-size:13px;color:#8896b3">{brow['Range']}</span>
                    <div style="flex:1;background:#111827;border-radius:4px;height:22px;overflow:hidden">
                        <div style="width:{pct}%;height:100%;background:{brow['Color']};border-radius:4px"></div>
                    </div>
                    <span style="width:40px;font-weight:700;font-family:monospace;color:{brow['Color']}">{brow['Stores']}</span>
                    </div>""",
                    unsafe_allow_html=True
                )
            
            st.markdown("<br>", unsafe_allow_html=True)
            
            # Below 100% stores
            below = kpis[kpis['fulfillment_pct'] < 100].sort_values('fulfillment_pct')
            if len(below) > 0:
                st.markdown(f"#### Stores Below 100% ({len(below)})")
                st.dataframe(
                    below[['store_id','store_name','dc','total_demand','total_allocated','short_qty','fulfillment_pct']],
                    use_container_width=True, hide_index=True
                )
            
            st.markdown("<br>", unsafe_allow_html=True)
            
            # Top pickers
            st.markdown("#### Top 15 Stores by Pickers Required")
            top_pk = kpis.nlargest(15, 'pickers_required')[['store_id','store_name','pickers_required','floors_used','total_pick_lines','total_allocated']]
            st.dataframe(top_pk, use_container_width=True, hide_index=True)
            
            # Hub summary
            if 'hub_cd' in kpis.columns:
                st.markdown("<br>", unsafe_allow_html=True)
                st.markdown("#### Hub Summary")
                hub_sum = kpis.groupby('hub_cd').agg(
                    stores=('store_id', 'nunique'),
                    total_demand=('total_demand', 'sum'),
                    total_allocated=('total_allocated', 'sum'),
                    total_pickers=('pickers_required', 'sum'),
                    avg_fulfillment=('fulfillment_pct', 'mean')
                ).round(1).sort_values('total_demand', ascending=False).reset_index()
                st.dataframe(hub_sum, use_container_width=True, hide_index=True)
    
    # ─── LOG TAB ───
    with tab5:
        if 'logs' in r and r['logs']:
            st.code("\n".join(r['logs']), language="text")


# ─── LANDING STATE (no results yet) ───
else:
    st.markdown("""
    <div style="text-align:center;padding:80px 20px;">
        <div style="font-size:60px;margin-bottom:16px;">📦</div>
        <h1 style="color:#e2e8f0;margin-bottom:8px;">RDC Allocation & Route Optimizer</h1>
        <p style="color:#4b5c7a;font-size:16px;max-width:500px;margin:0 auto;">
            Upload your allocation, bin stock, and store master files in the sidebar, then hit Run.
        </p>
        <br>
        <div style="display:flex;gap:40px;justify-content:center;color:#8896b3;font-size:13px;">
            <div>🎯 Optimal bin selection</div>
            <div>🗺️ Serpentine route optimization</div>
            <div>👷 Floor-isolated picker splitting</div>
            <div>📊 Store-level KPIs</div>
        </div>
    </div>
    """, unsafe_allow_html=True)
