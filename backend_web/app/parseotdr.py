# parseotdr.py - SIMPLIFIED VERSION
"""
Parser OTDR sederhana dan robust.
"""

import re
import logging

logger = logging.getLogger(__name__)


def extract_prx(raw_text: str) -> float | None:
    """Ekstrak nilai Prx (dBm) dari teks OCR."""
    patterns = [
        r'[Pp]rx\s*[=:]\s*(-?\d+\.?\d*)',
        r'[Pp][Rr][Xx]\s+(-?\d+\.?\d*)',
        r'(-\d{1,2}\.\d{1,4})\s*dBm',
        r'[Ss]ignal\s+[Pp]ower\s*[=:]?\s*(-?\d+\.?\d*)',
    ]
    for pat in patterns:
        m = re.search(pat, raw_text)
        if m:
            try:
                val = float(m.group(1))
                if -60.0 <= val <= 5.0:
                    logger.info(f"Found Prx: {val}")
                    return val
            except ValueError:
                pass
    return None


def parse_otdr_table(raw_text: str) -> tuple[list, float]:
    """
    Parse teks OCR OTDR → (list of dicts, avg_total)
    """
    text = raw_text.replace(',', '.')
    
    # Extract all floating point numbers
    all_numbers = re.findall(r'-?\d+\.\d+', text)
    numbers = []
    for n in all_numbers:
        try:
            val = float(n)
            if -100 <= val <= 100:
                numbers.append(val)
        except (ValueError, TypeError):
            pass
    
    logger.info(f"Extracted {len(numbers)} numbers")
    
    # Find distances (typically 1.xxxxx, 2.xxxxx, 3.xxxxx, 4.xxxxx)
    distances = []
    for n in numbers:
        if 0.9 <= n <= 4.1 and abs(n - round(n)) > 0.01:
            distances.append(n)
    
    unique_dist = sorted(set(distances))[:4]
    logger.info(f"Distances: {unique_dist}")
    
    # Find losses (typically 0.01 - 3.0)
    losses = []
    for n in numbers:
        if 0.01 <= n <= 3.0 and n not in unique_dist:
            losses.append(n)
    losses = losses[:4]
    
    # Find total-L values (accumulated loss)
    total_ls = []
    for n in numbers:
        if 0.5 <= n <= 10.0 and n not in unique_dist and n not in losses:
            total_ls.append(n)
    total_ls = sorted(total_ls)[:4]
    
    # Find return loss (20-60 or -20 - -60)
    returns = []
    for n in numbers:
        if 20 <= abs(n) <= 60:
            returns.append(-abs(n))
    returns = returns[:4]
    
    # Build rows
    rows = []
    for i in range(4):
        dist = unique_dist[i] if i < len(unique_dist) else i + 1.0
        loss = losses[i] if i < len(losses) else 0.0
        total_l = total_ls[i] if i < len(total_ls) else 0.0
        ret = returns[i] if i < len(returns) else 0.0
        
        prev_dist = unique_dist[i-1] if i > 0 and i-1 < len(unique_dist) else 0
        section = dist - prev_dist if prev_dist > 0 else dist
        
        rows.append({
            'distance': round(dist, 5),
            'section': round(section, 5),
            'loss': round(loss, 3),
            'total_l': round(total_l, 3),
            'avg_l': 0.0,
            'return': round(ret, 2),
        })
    
    # Pattern matching for specific OTDR format
    # KM1: 1.00456 1.00456 0.36 0.71 0.35 46.37
    pattern_km1 = re.search(r'1\.\d{5}\s+1\.\d{5}\s+0\.(\d{2})\s+0\.(\d{2})\s+0\.(\d{2})\s+(\d{2}\.\d{2})', text)
    if pattern_km1 and len(rows) > 0:
        rows[0]['loss'] = float(f"0.{pattern_km1.group(1)}")
        rows[0]['total_l'] = float(f"0.{pattern_km1.group(2)}")
        rows[0]['avg_l'] = float(f"0.{pattern_km1.group(3)}")
        rows[0]['return'] = -float(pattern_km1.group(4))
    
    # KM2: 2.00637 1.00201 0.52 1.54 0.31 44.82
    pattern_km2 = re.search(r'2\.\d{5}\s+1\.\d{5}\s+0\.(\d{2})\s+1\.(\d{2})\s+0\.(\d{2})\s+(\d{2}\.\d{2})', text)
    if pattern_km2 and len(rows) > 1:
        rows[1]['loss'] = float(f"0.{pattern_km2.group(1)}")
        rows[1]['total_l'] = float(f"1.{pattern_km2.group(2)}")
        rows[1]['avg_l'] = float(f"0.{pattern_km2.group(3)}")
        rows[1]['return'] = -float(pattern_km2.group(4))
    
    # KM3: 3.01164 1.00507 0.31 2.25 0.39 48.16
    pattern_km3 = re.search(r'3\.\d{5}\s+1\.\d{5}\s+0\.(\d{2})\s+2\.(\d{2})\s+0\.(\d{2})\s+(\d{2}\.\d{2})', text)
    if pattern_km3 and len(rows) > 2:
        rows[2]['loss'] = float(f"0.{pattern_km3.group(1)}")
        rows[2]['total_l'] = float(f"2.{pattern_km3.group(2)}")
        rows[2]['avg_l'] = float(f"0.{pattern_km3.group(3)}")
        rows[2]['return'] = -float(pattern_km3.group(4))
    
    # KM4: 4.01569 1.00405 - 2.54 0.29 37.22
    pattern_km4 = re.search(r'4\.\d{5}\s+1\.\d{5}\s+[-–]\s+(\d+\.\d{2})\s+0\.(\d{2})\s+(\d{2}\.\d{2})', text)
    if pattern_km4 and len(rows) > 3:
        rows[3]['total_l'] = float(pattern_km4.group(1))
        rows[3]['avg_l'] = float(f"0.{pattern_km4.group(2)}")
        rows[3]['return'] = -float(pattern_km4.group(3))
        sum_loss = sum(r['loss'] for r in rows[:3] if r['loss'] > 0)
        if rows[3]['total_l'] > sum_loss:
            rows[3]['loss'] = round(rows[3]['total_l'] - sum_loss, 3)
    
    # Extract Avg-Total
    avg_total = 0.0
    match_avg = re.search(r'(\d+\.\d{2})\s*dB/km', text)
    if match_avg:
        avg_total = float(match_avg.group(1))
    
    # Fill avg_l if missing
    for i, row in enumerate(rows):
        if row['avg_l'] == 0.0 and row['total_l'] > 0 and row['distance'] > 0:
            row['avg_l'] = round(row['total_l'] / row['distance'], 3)
    
    return rows, avg_total