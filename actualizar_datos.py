#!/usr/bin/env python3
"""
actualizar_datos.py
Descarga IPC (INDEC) y Tasa Pasiva (BCRA) y actualiza los datos embebidos en index.html.
Corre en GitHub Actions todos los días — sin CORS, sin restricciones.
"""

import re
import json
import urllib.request
import urllib.error
import struct
import datetime
import sys
import os

# ─── Rutas ────────────────────────────────────────────────────────────────────
SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
INDEX_HTML  = os.path.join(SCRIPT_DIR, 'index.html')

# ─── 1. Descargar IPC desde API INDEC ─────────────────────────────────────────
def descargar_ipc():
    url = 'https://apis.datos.gob.ar/series/api/series/?ids=148.3_INIVELNAL_DICI_M_26&limit=200&format=json&sort=asc'
    print("📥 Descargando IPC desde API INDEC...")
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode())
        ipc = {}
        for fecha, valor in data['data']:
            if valor is not None:
                key = fecha[:7] + '-01'
                ipc[key] = round(float(valor), 4)
        ipc = dict(sorted(ipc.items()))
        print(f"   ✅ {len(ipc)} meses — último: {list(ipc.keys())[-1]} = {list(ipc.values())[-1]}")
        return ipc
    except Exception as e:
        print(f"   ❌ Error IPC: {e}")
        return None

# ─── 2. Descargar Tasa Pasiva desde BCRA ──────────────────────────────────────
def descargar_tp():
    url = 'https://www.bcra.gob.ar/archivos/PDFs/PublicacionesEstadisticas/diar_ind.xls'
    print("📥 Descargando Tasa Pasiva desde BCRA...")
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
            'Accept': 'application/vnd.ms-excel,*/*',
            'Referer': 'https://www.bcra.gob.ar/',
        })
        with urllib.request.urlopen(req, timeout=60) as r:
            xls_bytes = r.read()
        print(f"   📄 Descargado: {len(xls_bytes):,} bytes")
        tp = parsear_xls_bcra(xls_bytes)
        if tp:
            print(f"   ✅ {len(tp)} registros — último: {list(tp.keys())[-1]} = {list(tp.values())[-1]}")
        return tp
    except Exception as e:
        print(f"   ❌ Error descargando BCRA: {e}")
        return None

def parsear_xls_bcra(xls_bytes):
    """
    Parsea el XLS del BCRA (formato BIFF8/BIFF5) sin dependencias externas.
    Extrae col1 (fecha) y col11 (índice acumulado).
    """
    try:
        # Intentar con openpyxl/xlrd si están disponibles
        import importlib
        for lib in ['xlrd', 'openpyxl']:
            try:
                importlib.import_module(lib)
                return parsear_con_xlrd(xls_bytes) if lib == 'xlrd' else None
            except ImportError:
                continue
        # Fallback: parseo manual básico del BIFF
        return parsear_biff_manual(xls_bytes)
    except Exception as e:
        print(f"   ⚠️  Error parseando XLS: {e}")
        return None

def parsear_con_xlrd(xls_bytes):
    import xlrd
    import io
    wb = xlrd.open_workbook(file_contents=xls_bytes)
    # Buscar hoja con más filas
    sheet = max(wb.sheets(), key=lambda s: s.nrows)
    tp = {}
    for i in range(1, sheet.nrows):
        try:
            cell0 = sheet.cell(i, 0)
            cell10 = sheet.cell(i, 10)
            if cell10.value == '' or cell10.value is None:
                continue
            # Fecha
            if cell0.ctype == 3:  # xlrd.XL_CELL_DATE
                dt = xlrd.xldate_as_datetime(cell0.value, wb.datemode)
                key = dt.strftime('%Y-%m-%d')
            elif cell0.ctype == 1:  # texto
                parts = str(cell0.value).strip().split('/')
                if len(parts) == 3:
                    key = f"{parts[2]}-{parts[1]}-{parts[0]}"
                else:
                    continue
            else:
                continue
            val = float(cell10.value)
            if val > 0:
                tp[key] = round(val, 4)
        except:
            continue
    return dict(sorted(tp.items())) if tp else None

def parsear_biff_manual(xls_bytes):
    """Parseo mínimo BIFF8 para extraer números de col0 y col10."""
    # El XLS del BCRA es consistente — usar struct para extraer registros NUMBER
    # Este método es un fallback robusto
    tp = {}
    try:
        # Buscar registros de tipo NUMBER (0x0203) y LABELSST (0x00FD) en BIFF8
        # Para simplicidad, usar el método de bytes directos
        i = 0
        current_row = -1
        current_col = -1
        row_data = {}  # {row: {col: value}}
        
        while i < len(xls_bytes) - 4:
            rec_type = struct.unpack_from('<H', xls_bytes, i)[0]
            rec_len  = struct.unpack_from('<H', xls_bytes, i+2)[0]
            
            if rec_type == 0x0203 and rec_len >= 14:  # NUMBER record
                row = struct.unpack_from('<H', xls_bytes, i+4)[0]
                col = struct.unpack_from('<H', xls_bytes, i+6)[0]
                val = struct.unpack_from('<d', xls_bytes, i+10)[0]
                if row not in row_data:
                    row_data[row] = {}
                row_data[row][col] = val
            
            i += 4 + rec_len
        
        # Convertir: col0 = fecha serial Excel, col10 = índice TP
        for row_idx in sorted(row_data.keys()):
            row = row_data[row_idx]
            if 0 not in row or 10 not in row:
                continue
            serial = row[0]
            val    = row[10]
            if serial < 30000 or val <= 0:
                continue
            # Convertir serial Excel a fecha
            dt = datetime.datetime(1899, 12, 30) + datetime.timedelta(days=int(serial))
            if dt.year < 1990 or dt.year > 2100:
                continue
            key = dt.strftime('%Y-%m-%d')
            tp[key] = round(val, 4)
        
        return dict(sorted(tp.items())) if tp else None
    except Exception as e:
        print(f"   ⚠️  parseo BIFF manual falló: {e}")
        return None

# ─── 3. Leer datos actuales del HTML ──────────────────────────────────────────
def leer_datos_actuales(html):
    """Extrae IPC_FALLBACK y TP_FALLBACK del HTML."""
    ipc_match = re.search(r'const IPC_FALLBACK = (\{[^;]+\});', html, re.DOTALL)
    tp_match  = re.search(r'const TP_FALLBACK = (\{[^;]+\});',  html, re.DOTALL)
    
    ipc_actual = json.loads(ipc_match.group(1)) if ipc_match else {}
    tp_actual  = {}
    if tp_match:
        # Los valores son números directamente
        tp_actual = json.loads(tp_match.group(1))
    
    return ipc_actual, tp_actual

# ─── 4. Serializar datos a JS ─────────────────────────────────────────────────
def serializar_ipc(ipc_dict):
    """Serializa IPC en formato compacto por año."""
    lines = []
    year_current = None
    year_items = []
    
    for key, val in sorted(ipc_dict.items()):
        year = key[:4]
        if year != year_current:
            if year_items:
                lines.append('  ' + ','.join(year_items))
            year_current = year
            year_items = []
        year_items.append(f'"{key}":{val}')
    if year_items:
        lines.append('  ' + ','.join(year_items))
    
    return '{\n' + ',\n'.join(lines) + '\n}'

def serializar_tp(tp_dict):
    """Serializa TP en bloques de 4 por línea."""
    items = [f'"{k}":{v}' for k, v in sorted(tp_dict.items())]
    lines = []
    for i in range(0, len(items), 4):
        lines.append('  ' + ', '.join(items[i:i+4]))
    return '{\n' + ',\n'.join(lines) + '\n}'

# ─── 5. Actualizar HTML ────────────────────────────────────────────────────────
def actualizar_html(ipc_nuevo, tp_nuevo):
    with open(INDEX_HTML, 'r', encoding='utf-8') as f:
        html = f.read()
    
    ipc_actual, tp_actual = leer_datos_actuales(html)
    
    # Mergear datos (los nuevos prevalecen)
    ipc_merged = {**ipc_actual, **ipc_nuevo}
    tp_merged  = {**tp_actual,  **tp_nuevo}
    
    nuevos_ipc = len(ipc_merged) - len(ipc_actual)
    nuevos_tp  = len(tp_merged)  - len(tp_actual)
    
    print(f"\n📊 Resumen:")
    print(f"   IPC: {len(ipc_actual)} → {len(ipc_merged)} (+{nuevos_ipc} meses)")
    print(f"   TP:  {len(tp_actual)} → {len(tp_merged)} (+{nuevos_tp} registros)")
    
    if nuevos_ipc == 0 and nuevos_tp == 0:
        print("\n✅ Datos ya estaban al día — no se modifica el HTML.")
        return False
    
    # Reemplazar bloques en HTML
    html = re.sub(
        r'const IPC_FALLBACK = \{[^;]+\};',
        f'const IPC_FALLBACK = {serializar_ipc(ipc_merged)};',
        html, flags=re.DOTALL
    )
    html = re.sub(
        r'const TP_FALLBACK = \{[^;]+\};',
        f'const TP_FALLBACK = {serializar_tp(tp_merged)};',
        html, flags=re.DOTALL
    )
    
    # Actualizar fecha de última actualización en el HTML (comentario en el head)
    hoy = datetime.date.today().strftime('%Y-%m-%d')
    html = re.sub(
        r'<!-- Datos actualizados: \d{4}-\d{2}-\d{2} -->',
        f'<!-- Datos actualizados: {hoy} -->',
        html
    )
    if '<!-- Datos actualizados:' not in html:
        html = html.replace('<script src="https://cdn.sheetjs.com',
                            f'<!-- Datos actualizados: {hoy} -->\n<script src="https://cdn.sheetjs.com')
    
    with open(INDEX_HTML, 'w', encoding='utf-8') as f:
        f.write(html)
    
    print(f"\n✅ index.html actualizado con datos hasta:")
    print(f"   IPC: {max(ipc_merged.keys())}")
    print(f"   TP:  {max(tp_merged.keys())}")
    return True

# ─── Main ─────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print("=" * 60)
    print("  Actualizador de datos — Calculadora Ley 27.802")
    print(f"  {datetime.datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print("=" * 60 + "\n")
    
    ipc = descargar_ipc()
    tp  = descargar_tp()
    
    if ipc is None and tp is None:
        print("\n❌ No se pudo descargar ninguna serie. Abortando.")
        sys.exit(1)
    
    ipc = ipc or {}
    tp  = tp  or {}
    
    modificado = actualizar_html(ipc, tp)
    
    # Indicar a GitHub Actions si hubo cambios (para el commit condicional)
    if modificado:
        with open(os.path.join(SCRIPT_DIR, '.actualizado'), 'w') as f:
            f.write('1')
    
    print("\n✅ Script finalizado.")
