"""
RDC Optimizer Engine
4-Phase: Bin Selection → Route Optimization → Picker Splitting → KPI Generation
"""

import re, time, tempfile, os
import pandas as pd
import numpy as np
from collections import defaultdict


def _save_temp(file_obj, suffix):
    """Save Streamlit upload to temp file for pyxlsb compatibility"""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    file_obj.seek(0)
    tmp.write(file_obj.read())
    tmp.close()
    return tmp.name


# ═══════════════════════════════════════════════════════
#  DATA LOADING
# ═══════════════════════════════════════════════════════

def load_allocation(file_obj):
    fname = file_obj.name.lower()

    if fname.endswith('.xlsb'):
        import pyxlsb
        tmp = _save_temp(file_obj, '.xlsb')
        try:
            rows = []
            with pyxlsb.open_workbook(tmp) as wb:
                sheet_name = next((s for s in wb.sheets if 'ALLOC' in s.upper()), wb.sheets[0])
                with wb.get_sheet(sheet_name) as sheet:
                    headers = None
                    for i, row in enumerate(sheet.rows()):
                        vals = [c.v for c in row]
                        if headers is None:
                            if any(str(v).strip() == 'VAR-ART' for v in vals if v):
                                headers = [str(v).strip() if v else '' for v in vals]
                                continue
                        elif headers:
                            obj = {}
                            for j, h in enumerate(headers):
                                if h and j < len(vals):
                                    obj[h] = vals[j]
                            rows.append(obj)
            df = pd.DataFrame(rows)
        finally:
            os.unlink(tmp)
    else:
        file_obj.seek(0)
        xls = pd.ExcelFile(file_obj)
        sheet = next((s for s in xls.sheet_names if 'ALLOC' in s.upper()), xls.sheet_names[0])
        raw = pd.read_excel(file_obj, sheet_name=sheet, header=None)
        hdr_idx = 0
        for i in range(min(10, len(raw))):
            if raw.iloc[i].astype(str).str.contains('VAR-ART').any():
                hdr_idx = i
                break
        file_obj.seek(0)
        df = pd.read_excel(file_obj, sheet_name=sheet, header=hdr_idx)

    col_map = {'ST-CD': 'store_id', 'ST-NM': 'store_name', 'VAR-ART': 'article_id',
               'PK-SZ': 'pack_size', 'FINAL VAR-ART ALLOC': 'demand_qty'}
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

    for col in ['store_id', 'store_name', 'article_id', 'demand_qty']:
        if col not in df.columns:
            raise ValueError(f"Missing column: {col}")

    if 'pack_size' not in df.columns:
        df['pack_size'] = 0

    df['store_id'] = df['store_id'].astype(str).str.strip()
    df['store_name'] = df['store_name'].astype(str).str.strip()
    df['article_id'] = df['article_id'].apply(lambda x: str(int(x)) if pd.notna(x) and isinstance(x, (int, float)) else str(x).strip())
    df['pack_size'] = df['pack_size'].fillna(0).astype(int)
    df['demand_qty'] = pd.to_numeric(df['demand_qty'], errors='coerce').fillna(0).astype(int)
    df = df[df['demand_qty'] > 0]

    return df[['store_id', 'store_name', 'article_id', 'pack_size', 'demand_qty']]


def load_bin_stock(file_obj):
    fname = file_obj.name.lower()
    all_data = []

    if fname.endswith('.xlsb'):
        import pyxlsb
        tmp = _save_temp(file_obj, '.xlsb')
        try:
            with pyxlsb.open_workbook(tmp) as wb:
                for sn in wb.sheets:
                    if sn in ('DW01', 'DH24'):
                        rows = []
                        with wb.get_sheet(sn) as sheet:
                            headers = None
                            for i, row in enumerate(sheet.rows()):
                                vals = [c.v for c in row]
                                if i == 0:
                                    headers = [str(v).strip() if v else '' for v in vals]
                                    continue
                                obj = {}
                                for j, h in enumerate(headers):
                                    if h and j < len(vals):
                                        obj[h] = vals[j]
                                rows.append(obj)
                        df = pd.DataFrame(rows)
                        df['dc'] = sn
                        all_data.append((sn, df))
        finally:
            os.unlink(tmp)
    else:
        file_obj.seek(0)
        xls = pd.ExcelFile(file_obj)
        for sn in xls.sheet_names:
            if sn in ('DW01', 'DH24'):
                df = pd.read_excel(file_obj, sheet_name=sn)
                df['dc'] = sn
                all_data.append((sn, df))

    if not all_data:
        raise ValueError("No DW01/DH24 sheets found in bin stock file")

    frames = []
    sheet_info = {}
    for sn, df in all_data:
        col_map = {'WERKS': 'dc_orig', 'LGPLA': 'bin_id', 'MATNR': 'article_id',
                    'CURR_STK': 'available_qty', 'LGTYP': 'bin_type', 'STATUS_FE': 'article_status'}
        df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
        df['bin_id'] = df['bin_id'].astype(str).str.strip()
        df['article_id'] = df['article_id'].apply(lambda x: str(int(x)) if pd.notna(x) and isinstance(x, (int, float)) else str(x).strip())
        df['available_qty'] = pd.to_numeric(df['available_qty'], errors='coerce').fillna(0).astype(int)
        df['dc'] = df['dc'].astype(str).str.strip()
        for c in ['bin_type', 'article_status']:
            if c not in df.columns:
                df[c] = ''
            df[c] = df[c].fillna('').astype(str).str.strip()
        df = df[df['available_qty'] > 0]
        sheet_info[sn] = len(df)
        frames.append(df[['dc', 'bin_id', 'article_id', 'available_qty', 'bin_type', 'article_status']])

    return pd.concat(frames, ignore_index=True), sheet_info


def load_store_master(file_obj):
    file_obj.seek(0)
    df = pd.read_excel(file_obj)
    rename = {}
    for col in df.columns:
        cl = str(col).strip().upper().replace(' ', '_')
        if cl in ('ST_CD', 'ST-CD'): rename[col] = 'store_id'
        elif cl in ('ST_NM', 'ST-NM'): rename[col] = 'store_name'
        elif cl == 'RDC_CD': rename[col] = 'dc'
        elif cl == 'RDC_NM': rename[col] = 'dc_name'
        elif cl == 'HUB_CD': rename[col] = 'hub_cd'
        elif cl == 'HUB_NM': rename[col] = 'hub_name'

    df = df.rename(columns=rename)
    for col in ['store_id', 'store_name', 'dc', 'hub_cd', 'hub_name']:
        if col not in df.columns:
            df[col] = ''
        df[col] = df[col].fillna('').astype(str).str.strip()
    if 'dc_name' not in df.columns:
        df['dc_name'] = ''

    return df[['store_id', 'store_name', 'dc', 'dc_name', 'hub_cd', 'hub_name']]


# ═══════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════

def parse_bin_location(bin_id):
    match = re.match(r'^([A-Z])(\d+)-(\d{2})(\d{2})-([A-Z])(\d+)$', str(bin_id))
    if match:
        return (match.group(1), int(match.group(2)), int(match.group(3)),
                int(match.group(4)), match.group(5), int(match.group(6)))
    s = str(bin_id)
    if len(s) >= 2 and s[0].isalpha() and s[1].isdigit():
        return (s[0], int(s[1]), 0, 0, '', 0)
    return ('', 0, 0, 0, '', 0)

def _sn(s): return ord(s) - ord('A') + 1 if s and isinstance(s, str) and len(s) == 1 else 0
def _ln(l): return ord(l) - ord('A') + 1 if l and isinstance(l, str) and len(l) == 1 else 0

def walk_key(section, floor, row, gondola, level, position):
    g = (99 - gondola) if (row and row % 2 == 0) else (gondola or 0)
    return _sn(section) * 10_000_000 + (row or 0) * 100_000 + g * 1000 + _ln(level) * 10 + (position or 0)

def round_to_pack(qty, ps):
    if ps <= 0: return qty
    lo = (qty // ps) * ps
    hi = lo + ps
    return max(lo, ps) if abs(qty - lo) <= abs(qty - hi) else hi


# ═══════════════════════════════════════════════════════
#  4-PHASE ENGINE
# ═══════════════════════════════════════════════════════

def run_optimizer(demand_df, stock_df, store_master_df, config, progress_cb=None):
    logs = []
    def log(msg):
        logs.append(msg)
        if progress_cb: progress_cb(msg=msg)

    t_start = time.time()
    target_qty = config.get('target_qty', 400)
    soft_cap = config.get('soft_cap', 600)
    force_dc = config.get('force_dc', None)
    exclude_types = config.get('exclude_bin_types', [])
    exclude_statuses = config.get('exclude_statuses', ['Empty'])
    cand_max = config.get('candidate_bins', 50)

    # ═══ PHASE 1 ═══
    log("═══ Phase 1: Bin Selection ═══")
    t0 = time.time()

    filtered = stock_df.copy()
    if exclude_types:
        filtered = filtered[~filtered['bin_type'].isin(exclude_types)]
        log(f"  Excluded bin types: {len(filtered):,} bins remain")
    if exclude_statuses:
        filtered = filtered[~filtered['article_status'].isin(exclude_statuses)]
        log(f"  Excluded statuses: {len(filtered):,} bins remain")

    if force_dc:
        store_dc_map = {sid: force_dc for sid in store_master_df['store_id']}
        filtered = filtered[filtered['dc'] == force_dc]
        log(f"  Forced DC: {force_dc} → {len(filtered):,} bins")
    else:
        store_dc_map = dict(zip(store_master_df['store_id'], store_master_df['dc']))

    demand = demand_df.copy()
    demand['dc'] = demand['store_id'].map(store_dc_map)
    demand = demand[demand['dc'].notna()]
    log(f"  Demand lines: {len(demand):,}")

    ledger = {}
    for _, r in filtered.iterrows():
        key = (r['dc'], r['bin_id'], r['article_id'])
        ledger[key] = ledger.get(key, 0) + r['available_qty']
    log(f"  Ledger: {len(ledger):,} entries")

    article_bins = defaultdict(list)
    for (dc, bid, aid), qty in ledger.items():
        article_bins[(dc, aid)].append((bid, qty))

    bin_locs = {}
    for _, r in filtered.iterrows():
        if r['bin_id'] not in bin_locs:
            bin_locs[r['bin_id']] = parse_bin_location(r['bin_id'])

    selected_bins = set()
    allocations = []
    shortages = []
    demand = demand.sort_values('demand_qty', ascending=False)
    total = len(demand)
    last_pct = 0

    for idx, (_, row) in enumerate(demand.iterrows()):
        pct = int(idx / total * 100)
        if pct >= last_pct + 10:
            log(f"  Progress: {pct}%")
            if progress_cb: progress_cb(pct=int(35 + pct * 0.4))
            last_pct = pct

        sid, sname, aid = row['store_id'], row['store_name'], row['article_id']
        dq, ps, dc = row['demand_qty'], row['pack_size'], row['dc']

        target = round_to_pack(dq, ps)
        remaining = target
        cands = article_bins.get((dc, aid), [])

        if not cands:
            shortages.append({'store_id': sid, 'store_name': sname, 'article_id': aid,
                              'demand_qty': dq, 'target_qty': target, 'allocated_qty': 0,
                              'short_qty': target, 'reason': 'no_stock'})
            continue

        scored = []
        for bid, _ in cands:
            key = (dc, bid, aid)
            avail = ledger.get(key, 0)
            if avail <= 0: continue
            score = -abs(avail - remaining)
            if avail == remaining: score += 1_000_000
            if avail <= remaining: score += 500_000
            if bid in selected_bins: score += 100_000
            scored.append((score, bid, avail, key))
        scored.sort(key=lambda x: -x[0])

        alloc_total = 0
        for score, bid, avail, key in scored[:cand_max]:
            if remaining <= 0: break
            pick = min(avail, remaining)
            if ps > 0 and pick > 0:
                pick = round_to_pack(pick, ps)
                pick = min(pick, avail)
            if pick <= 0: continue

            loc = bin_locs.get(bid, ('', 0, 0, 0, '', 0))
            allocations.append({
                'store_id': sid, 'store_name': sname, 'article_id': aid,
                'dc': dc, 'bin_id': bid, 'pick_qty': pick, 'pack_size': ps,
                'section': loc[0], 'floor': loc[1], 'row': loc[2],
                'gondola': loc[3], 'level': loc[4], 'position': loc[5],
            })
            ledger[key] -= pick
            remaining -= pick
            alloc_total += pick
            selected_bins.add(bid)

        if remaining > 0:
            shortages.append({'store_id': sid, 'store_name': sname, 'article_id': aid,
                              'demand_qty': dq, 'target_qty': target, 'allocated_qty': alloc_total,
                              'short_qty': remaining,
                              'reason': 'no_stock' if alloc_total == 0 else 'insufficient_stock'})

    alloc_df = pd.DataFrame(allocations)
    short_df = pd.DataFrame(shortages)
    log(f"  ✓ Phase 1: {len(alloc_df):,} picks, {len(short_df):,} shortages ({time.time()-t0:.1f}s)")
    if progress_cb: progress_cb(pct=78)

    if len(alloc_df) == 0:
        return alloc_df, short_df, pd.DataFrame(), logs

    # ═══ PHASE 2 ═══
    log("═══ Phase 2: Route Optimization ═══")
    t0 = time.time()
    alloc_df = alloc_df.copy()
    alloc_df['walk_key'] = alloc_df.apply(
        lambda r: walk_key(r['section'], r['floor'], r['row'], r['gondola'], r['level'], r['position']), axis=1
    ).astype(int)
    alloc_df = alloc_df.sort_values(['store_id', 'dc', 'floor', 'walk_key'])
    alloc_df['pick_sequence_no'] = alloc_df.groupby(['store_id', 'dc', 'floor']).cumcount() + 1
    log(f"  ✓ Phase 2: Serpentine routes ({time.time()-t0:.1f}s)")
    if progress_cb: progress_cb(pct=83)

    # ═══ PHASE 3 ═══
    log(f"═══ Phase 3: Picker Splitting (target={target_qty}, cap={soft_cap}) ═══")
    t0 = time.time()
    alloc_df['picker_no'] = 0

    for (store, dc, floor), gidx in alloc_df.groupby(['store_id', 'dc', 'floor']).groups.items():
        group = alloc_df.loc[gidx].sort_values('pick_sequence_no')
        tot = group['pick_qty'].sum()
        if tot <= soft_cap:
            alloc_df.loc[gidx, 'picker_no'] = 1
            continue
        rem, np_ = tot, 0
        while rem > soft_cap:
            rem -= target_qty
            np_ += 1
        np_ += 1
        pk, run_ = 1, 0
        for ix in group.index.tolist():
            run_ += alloc_df.loc[ix, 'pick_qty']
            alloc_df.loc[ix, 'picker_no'] = pk
            if run_ >= target_qty and pk < np_:
                pk += 1
                run_ = 0

    for (store, dc), sidx in alloc_df.groupby(['store_id', 'dc']).groups.items():
        sg = alloc_df.loc[sidx].sort_values(['floor', 'pick_sequence_no'])
        offset, prev_floor, max_pk = 0, None, 0
        for ix in sg.index:
            cf = alloc_df.loc[ix, 'floor']
            if prev_floor is not None and cf != prev_floor:
                offset = max_pk
            if prev_floor is None or cf != prev_floor:
                max_pk = offset
            npk = offset + alloc_df.loc[ix, 'picker_no']
            alloc_df.loc[ix, 'picker_no'] = npk
            max_pk = max(max_pk, npk)
            prev_floor = cf

    total_pickers = int(alloc_df.groupby(['store_id', 'dc'])['picker_no'].max().sum())
    log(f"  ✓ Phase 3: {total_pickers} pickers, floor-isolated ({time.time()-t0:.1f}s)")
    if progress_cb: progress_cb(pct=90)

    # ═══ PHASE 4 ═══
    log("═══ Phase 4: KPIs ═══")
    sm = store_master_df
    maps = {
        'name': dict(zip(sm['store_id'], sm['store_name'])),
        'hub_cd': dict(zip(sm['store_id'], sm['hub_cd'])),
        'hub_name': dict(zip(sm['store_id'], sm['hub_name'])),
    }

    dem_sum = demand_df.groupby('store_id').agg(
        total_demand=('demand_qty', 'sum'), total_skus=('article_id', 'nunique')
    ).reset_index()

    kpi = alloc_df.groupby(['store_id', 'dc']).agg(
        total_allocated=('pick_qty', 'sum'), bins_visited=('bin_id', 'nunique'),
        total_pick_lines=('pick_qty', 'count'), pickers_required=('picker_no', 'max'),
        unique_articles=('article_id', 'nunique'), floors_used=('floor', 'nunique')
    ).reset_index()

    multi = alloc_df.groupby(['store_id', 'article_id']).size().reset_index(name='n')
    split_r = (multi[multi['n'] > 1].groupby('store_id').size() /
               multi.groupby('store_id').size() * 100).fillna(0)

    kpi['store_name'] = kpi['store_id'].map(maps['name'])
    kpi['hub_cd'] = kpi['store_id'].map(maps['hub_cd'])
    kpi['hub_name'] = kpi['store_id'].map(maps['hub_name'])
    kpi = kpi.merge(dem_sum, on='store_id', how='left')
    kpi['fulfillment_pct'] = (kpi['total_allocated'] / kpi['total_demand'] * 100).round(1)
    kpi['split_rate_pct'] = kpi['store_id'].map(split_r).fillna(0).round(1)
    kpi['short_qty'] = (kpi['total_demand'] - kpi['total_allocated']).clip(lower=0)

    cols = ['store_id', 'store_name', 'dc', 'hub_cd', 'hub_name', 'total_demand',
            'total_allocated', 'short_qty', 'fulfillment_pct', 'bins_visited',
            'total_pick_lines', 'unique_articles', 'floors_used', 'pickers_required',
            'split_rate_pct']
    kpi = kpi[[c for c in cols if c in kpi.columns]]
    for c in ['total_demand', 'total_allocated', 'short_qty', 'bins_visited',
              'total_pick_lines', 'unique_articles', 'floors_used', 'pickers_required']:
        if c in kpi.columns:
            kpi[c] = kpi[c].fillna(0).astype(int)

    log(f"  ✓ Phase 4: {len(kpi)} store KPIs")

    hub_map = dict(zip(sm['store_id'], sm['hub_cd']))
    alloc_df['hub'] = alloc_df['store_id'].map(hub_map)
    out_cols = ['store_id', 'store_name', 'hub', 'dc', 'floor', 'picker_no',
                'pick_sequence_no', 'section', 'row', 'gondola', 'level', 'position',
                'bin_id', 'article_id', 'pick_qty', 'pack_size', 'walk_key']
    for c in out_cols:
        if c not in alloc_df.columns:
            alloc_df[c] = ''
    picklist = alloc_df[out_cols].copy()
    for c in ['floor', 'picker_no', 'pick_sequence_no', 'row', 'gondola',
              'position', 'pick_qty', 'pack_size', 'walk_key']:
        picklist[c] = picklist[c].fillna(0).astype(int)

    ta = int(picklist['pick_qty'].sum())
    td = int(demand_df['demand_qty'].sum())
    elapsed = time.time() - t_start
    log(f"\n{'='*45}")
    log(f"✓ COMPLETE ({elapsed:.1f}s)")
    log(f"  Pick lines:  {len(picklist):,}")
    log(f"  Allocated:   {ta:,} / {td:,} = {ta/td*100:.1f}%")
    log(f"  Shortages:   {len(short_df):,}")
    log(f"  Bins used:   {picklist['bin_id'].nunique():,}")
    log(f"  Pickers:     {total_pickers}")
    log(f"{'='*45}")
    if progress_cb: progress_cb(pct=100)

    return picklist, short_df, kpi, logs
