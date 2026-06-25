// frontend/src/components/MainDashboard.tsx
// Dashboard pakai dataset SOR — grafik trace t0..tN sebagai monitoring live.
// Detection page (data OTDR) TIDAK diubah.
// Semua logika sorTrace dan useTracePlayback digabung langsung di file ini.

import { useState, useEffect, useRef, useCallback } from 'react';
import {
  Activity, CheckCircle2, AlertTriangle,
} from 'lucide-react';
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, PieChart, Pie, Cell,
} from 'recharts';
import { syncFromSheets } from '../services/api';
import { useSlide } from '../Context/SlideContext';
import NetworkTopology from '../components/NetworkTopology';

// ─────────────────────────────────────────────────────────────
// TIPE DATA SOR
// ─────────────────────────────────────────────────────────────

/** Satu row dari dataset SOR setelah diparsing */
type SorTraceRow = {
  rowIndex: number;
  tracePoints: number[];         // nilai dari kolom t0..tN (sudah diurutkan by angka)
  label: string;                 // "Normal" atau nama fault
  metadata: Record<string, any>; // sisa kolom lain (id, timestamp, dsb)
};

/** Satu titik yang ditampilkan di grafik */
type DashboardPoint = {
  timestamp: string;   // waktu simulasi HH:mm:ss
  value: number;       // nilai titik trace
  pointIndex: number;  // index kolom t ke-berapa
  rowIndex: number;    // dari row ke-berapa
};

// ─────────────────────────────────────────────────────────────
// FUNGSI PARSER KOLOM t0..tN
// ─────────────────────────────────────────────────────────────

/**
 * Ekstrak semua kolom t<angka> dari satu raw row, diurutkan by angka.
 * t2 selalu sebelum t10. Nilai null/kosong di-handle dengan aman (jadi 0).
 */
function extractTracePoints(row: Record<string, any>): number[] {
  const tKeys = Object.keys(row).filter(k => /^t\d+$/.test(k));
  tKeys.sort((a, b) => parseInt(a.slice(1), 10) - parseInt(b.slice(1), 10));
  return tKeys.map(k => {
    const v = row[k];
    if (v === null || v === undefined || v === '') return 0;
    const parsed = parseFloat(v);
    return isNaN(parsed) ? 0 : parsed;
  });
}

/**
 * Konversi array raw row menjadi array SorTraceRow.
 * Kolom label dicari dengan urutan prioritas: label → fault_type → class → status.
 */
function parseSorDataset(rawRows: Record<string, any>[]): SorTraceRow[] {
  return rawRows.map((row, idx) => {
    const tracePoints = extractTracePoints(row);
    const labelValue = row['label'] ?? row['fault_type'] ?? row['class'] ?? row['status'] ?? 'Normal';
    const metadata: Record<string, any> = {};
    Object.keys(row).forEach(k => { if (!/^t\d+$/.test(k)) metadata[k] = row[k]; });
    return { rowIndex: idx, tracePoints, label: String(labelValue), metadata };
  });
}

// ─────────────────────────────────────────────────────────────
// FUNGSI HELPER STATUS
// ─────────────────────────────────────────────────────────────

/** Cek apakah label row adalah kondisi gangguan */
function isGangguanLabel(label: string): boolean {
  return label.toLowerCase() !== 'normal';
}

/** Map label ke severity untuk UI */
function getLabelSeverity(label: string): 'Normal' | 'Warning' | 'Critical' {
  const l = label.toLowerCase();
  if (l === 'normal') return 'Normal';
  if (l.includes('cut') || l.includes('putus')) return 'Critical';
  return 'Warning';
}

// ─────────────────────────────────────────────────────────────
// PLAYBACK CONTROLLER (inline, menggantikan useTracePlayback)
// ─────────────────────────────────────────────────────────────

/**
 * Hook playback trace SOR: menampilkan titik t0..tN satu per satu,
 * lalu pindah ke row berikutnya setelah row selesai (loop).
 *
 * @param rows        - dataset SOR yang sudah diparsing
 * @param intervalMs  - jarak antar titik dalam ms (default 200ms)
 * @param maxBuffer   - max titik yang ditampilkan sekaligus (default 120)
 */
function useTracePlayback(rows: SorTraceRow[], intervalMs = 200, maxBuffer = 120) {
  const [currentRowIndex, setCurrentRowIndex] = useState(0);
  const [currentPointIndex, setCurrentPointIndex] = useState(0);
  const [displayedPoints, setDisplayedPoints] = useState<DashboardPoint[]>([]);
  const [isPlaying, setIsPlaying] = useState(true);

  // Waktu mulai row aktif untuk membuat timestamp konsisten dalam 1 row
  const rowStartTimeRef = useRef<number>(Date.now());
  const prevRowRef = useRef(currentRowIndex);

  // Reset buffer & counter saat row berganti
  useEffect(() => {
    if (prevRowRef.current !== currentRowIndex) {
      prevRowRef.current = currentRowIndex;
      rowStartTimeRef.current = Date.now();
      setCurrentPointIndex(0);
      setDisplayedPoints([]);
    }
  }, [currentRowIndex]);

  // Interval utama: tambah 1 titik tiap intervalMs
  useEffect(() => {
    if (!isPlaying || rows.length === 0) return;

    const timer = setInterval(() => {
      setCurrentPointIndex(prevPoint => {
        const row = rows[currentRowIndex];
        if (!row) return 0;

        // Row selesai → pindah ke row berikutnya (loop ke awal)
        if (prevPoint >= row.tracePoints.length) {
          setCurrentRowIndex(prev => (prev + 1) % rows.length);
          return 0;
        }

        // Format timestamp HH:mm:ss untuk titik ini
        const d = new Date(rowStartTimeRef.current + prevPoint * 1000);
        const ts = `${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}:${String(d.getSeconds()).padStart(2,'0')}`;

        const newPoint: DashboardPoint = {
          timestamp: ts,
          value: row.tracePoints[prevPoint],
          pointIndex: prevPoint,
          rowIndex: currentRowIndex,
        };

        // Tambah ke buffer, potong jika melebihi maxBuffer
        setDisplayedPoints(prev => {
          const updated = [...prev, newPoint];
          return updated.length > maxBuffer ? updated.slice(updated.length - maxBuffer) : updated;
        });

        return prevPoint + 1;
      });
    }, intervalMs);

    return () => clearInterval(timer);
  }, [isPlaying, currentRowIndex, rows.length, intervalMs, maxBuffer]);

  const pause = useCallback(() => setIsPlaying(false), []);
  const resume = useCallback(() => setIsPlaying(true), []);

  return {
    displayedPoints,
    currentRowIndex,
    currentPointIndex,
    activeRow: rows[currentRowIndex] ?? null,
    isPlaying,
    progressInRow: currentPointIndex,
    totalPointsInRow: rows[currentRowIndex]?.tracePoints.length ?? 0,
    pause,
    resume,
  };
}

// ─────────────────────────────────────────────────────────────
// MOCK DATA GENERATOR — hapus saat /api/sor-data sudah tersedia
// ─────────────────────────────────────────────────────────────

function generateMockSorData(numRows: number, numPoints: number): Record<string, any>[] {
  const labels = ['Normal','Normal','Normal','Bending','Normal','Bad Splice','Normal','Normal','Nearly Cut','Normal'];
  return Array.from({ length: numRows }, (_, rowIdx) => {
    const row: Record<string, any> = { id: rowIdx + 1, label: labels[rowIdx % labels.length] };
    let base = -10;
    for (let t = 0; t < numPoints; t++) {
      base -= Math.random() * 0.3;
      const noise = (Math.random() - 0.5) * 0.5;
      const isFault = labels[rowIdx % labels.length] !== 'Normal';
      const spike = isFault && t === Math.floor(numPoints * 0.6) ? -5 : 0;
      row[`t${t}`] = parseFloat((base + noise + spike).toFixed(3));
    }
    return row;
  });
}

// ─────────────────────────────────────────────────────────────
// KONSTANTA
// ─────────────────────────────────────────────────────────────

const COLORS = ['#10b981', '#f59e0b', '#3b82f6', '#ef4444', '#8b5cf6', '#ec4899'];
const API_BASE = import.meta.env.VITE_API_URL || 'https://optim-api-ckfhb5heg3f3btgz.southeastasia-01.azurewebsites.net';
const TRACE_INTERVAL_MS = 200; // ms antar titik di grafik
const CHART_BUFFER = 120;      // max titik di chart sekaligus

// ─────────────────────────────────────────────────────────────
// KOMPONEN PEMBANTU
// ─────────────────────────────────────────────────────────────

interface MainDashboardProps {
  refreshTrigger?: number;
  onDataChange?: () => void;
}

const StatusBadge = ({ status }: { status: string | null | undefined }) => {
  const cfg: Record<string, string> = {
    Normal  : 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
    Warning : 'bg-amber-500/15 text-amber-400 border-amber-500/30',
    Critical: 'bg-red-500/15 text-red-400 border-red-500/30',
  };
  const dot: Record<string, string> = {
    Normal  : 'bg-emerald-400',
    Warning : 'bg-amber-400',
    Critical: 'bg-red-400',
  };
  const s = status || 'Warning';
  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-black uppercase border ${cfg[s] || cfg.Warning}`}>
      <span className={`w-1.5 h-1.5 rounded-full animate-pulse ${dot[s] || dot.Warning}`} />
      {s}
    </span>
  );
};

// ─────────────────────────────────────────────────────────────
// KOMPONEN UTAMA
// ─────────────────────────────────────────────────────────────

const MainDashboard = ({ refreshTrigger, onDataChange }: MainDashboardProps) => {
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [sorRows, setSorRows] = useState<SorTraceRow[]>([]);

  const { setCurrentIndex, totalData, setTotalData, setAutoPlay } = useSlide();

  // Playback controller — semua state trace ada di sini
  const {
    displayedPoints,
    currentRowIndex,
    currentPointIndex,
    activeRow,
    progressInRow,
    totalPointsInRow,
  } = useTracePlayback(sorRows, TRACE_INTERVAL_MS, CHART_BUFFER);

  // Sinkronkan row aktif ke SlideContext (progress bar)
  useEffect(() => {
    if (sorRows.length > 0) setCurrentIndex(currentRowIndex);
  }, [currentRowIndex, sorRows.length, setCurrentIndex]);

  // ── Fetch data SOR ──────────────────────────────────────────
  const fetchSorData = async () => {
    try {
      const token = localStorage.getItem('token');
      if (!token) return;

      const res = await fetch(`${API_BASE}/api/sor-data?limit=500`, {
        headers: { Authorization: `Bearer ${token}` },
      });

      if (res.ok) {
        const json = await res.json();
        const raw: Record<string, any>[] = json?.data ?? [];
        if (raw.length > 0) {
          const parsed = parseSorDataset(raw);
          setSorRows(parsed);
          setTotalData(parsed.length);
          setAutoPlay(true);
          return;
        }
      }

      // Fallback mock — hapus saat endpoint /api/sor-data sudah tersedia
      console.warn('[Dashboard] /api/sor-data belum tersedia – memakai mock data SOR');
      const parsed = parseSorDataset(generateMockSorData(10, 50));
      setSorRows(parsed);
      setTotalData(parsed.length);
      setAutoPlay(true);
    } catch (err) {
      console.error('Fetch SOR error:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchSorData(); }, []);
  useEffect(() => { if (refreshTrigger && refreshTrigger > 0) fetchSorData(); }, [refreshTrigger]);

  const handleSync = async () => {
    setSyncing(true);
    try {
      const result = await syncFromSheets();
      alert(`Sync selesai! ${result.saved} baris berhasil`);
      await fetchSorData();
      if (onDataChange) onDataChange();
    } catch {
      alert('Gagal sync data');
    } finally {
      setSyncing(false);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-[#14213d] flex flex-col items-center justify-center gap-4">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500" />
        <p className="text-white text-sm">Memuat data monitoring...</p>
      </div>
    );
  }

  // ── Derived state ────────────────────────────────────────────
  const progressPercent  = totalData > 0 ? ((currentRowIndex + 1) / totalData) * 100 : 0;
  const activeLabel      = activeRow?.label ?? 'Normal';
  const isGangguan       = isGangguanLabel(activeLabel);
  const severity         = getLabelSeverity(activeLabel);
  const passedRows       = sorRows.slice(0, currentRowIndex + 1);
  const normalCount      = passedRows.filter(r => !isGangguanLabel(r.label)).length;
  const gangguanCount    = passedRows.length - normalCount;
  const lastValue        = displayedPoints.length > 0 ? displayedPoints[displayedPoints.length - 1].value : null;

  const faultMap: Record<string, number> = {};
  passedRows.forEach(r => { const k = r.label || 'Unknown'; faultMap[k] = (faultMap[k] || 0) + 1; });
  const faultDistribution = Object.entries(faultMap).map(([name, value]) => ({ name, value }));

  // ── RENDER ───────────────────────────────────────────────────
  return (
    <div className="min-h-screen bg-[#14213d] text-slate-300 font-sans pb-20 w-full">
      <main className="p-6 w-full space-y-10">

        {/* Progress + Sync */}
        <div className="flex justify-between items-center gap-4">
          <div className="flex-1 bg-[#1e2f50] border border-[#3b4f6e] rounded-2xl p-4">
            <div className="flex justify-between items-center mb-2">
              <span className="text-xs text-white">Slide Show Progress (SOR Trace)</span>
              <span className="text-xs text-white font-mono">Row {currentRowIndex + 1} / {totalData}</span>
            </div>
            <div className="w-full bg-slate-600 rounded-full h-2">
              <div className="bg-emerald-500 h-2 rounded-full transition-all duration-500" style={{ width: `${progressPercent}%` }} />
            </div>
          </div>
          <button
            onClick={handleSync}
            disabled={syncing}
            className="px-4 py-2 bg-emerald-600 hover:bg-emerald-700 disabled:bg-slate-600 rounded-xl text-xs font-bold uppercase flex items-center gap-2 whitespace-nowrap"
          >
            {syncing ? 'Syncing...' : 'Sync from Sheets'}
          </button>
        </div>

        {/* Summary Stats */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          <div className="bg-[#1e2f50] border border-[#3b4f6e] p-6 rounded-[1.8rem] flex justify-between items-center">
            <div className="flex items-center gap-4">
              <div className="p-2.5 rounded-xl bg-blue-500/20 text-blue-400"><Activity size={20} /></div>
              <span className="text-lg text-white">Total Trace</span>
            </div>
            <span className="text-3xl font-black text-blue-400">{passedRows.length}</span>
          </div>
          <div className="bg-[#1e2f50] border border-[#3b4f6e] p-6 rounded-[1.8rem] flex justify-between items-center">
            <div className="flex items-center gap-4">
              <div className="p-2.5 rounded-xl bg-emerald-500/20 text-emerald-400"><CheckCircle2 size={20} /></div>
              <span className="text-lg text-white">Normal</span>
            </div>
            <span className="text-3xl font-black text-emerald-400">{normalCount}</span>
          </div>
          <div className="bg-[#1e2f50] border border-[#3b4f6e] p-6 rounded-[1.8rem] flex justify-between items-center">
            <div className="flex items-center gap-4">
              <div className="p-2.5 rounded-xl bg-amber-500/20 text-amber-400"><AlertTriangle size={20} /></div>
              <span className="text-lg text-white">Event Detected</span>
            </div>
            <span className="text-3xl font-black text-amber-400">{gangguanCount}</span>
          </div>
        </div>

        {/* Banner gangguan — hanya muncul saat row aktif bukan Normal */}
        {isGangguan && (
          <div className={`flex items-center gap-3 px-5 py-3 rounded-2xl border ${
            severity === 'Critical' ? 'bg-red-500/10 border-red-500/30' : 'bg-amber-500/10 border-amber-500/30'
          }`}>
            <div className={`w-2.5 h-2.5 rounded-full animate-pulse ${severity === 'Critical' ? 'bg-red-500' : 'bg-amber-500'}`} />
            <span className={`text-sm font-bold ${severity === 'Critical' ? 'text-red-400' : 'text-amber-400'}`}>
              {severity === 'Critical' ? '⚠ CRITICAL — ' : '⚠ GANGGUAN TERDETEKSI — '}
              {activeLabel.toUpperCase()}
            </span>
          </div>
        )}

        {/* Panel status trace aktif */}
        <div className="bg-[#1e2f50] border border-[#3b4f6e] rounded-[2rem] p-6">
          <h3 className="text-xs font-black text-white uppercase tracking-widest mb-4 flex items-center gap-2">
            <div className="w-1.5 h-4 bg-blue-500 rounded-full" />
            Active Trace Status
          </h3>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
            <div className="flex flex-col gap-1">
              <span className="text-[10px] text-slate-400 uppercase tracking-wide">Current Trace</span>
              <span className="text-xl font-black text-white">Row {currentRowIndex + 1}</span>
            </div>
            <div className="flex flex-col gap-1">
              <span className="text-[10px] text-slate-400 uppercase tracking-wide">Current Point</span>
              <span className="text-xl font-black text-white">t{currentPointIndex}</span>
            </div>
            <div className="flex flex-col gap-1">
              <span className="text-[10px] text-slate-400 uppercase tracking-wide">Point Progress</span>
              <span className="text-xl font-black text-white">{progressInRow} / {totalPointsInRow}</span>
            </div>
            <div className="flex flex-col gap-1">
              <span className="text-[10px] text-slate-400 uppercase tracking-wide">Last Value</span>
              <span className={`text-xl font-black ${isGangguan ? 'text-amber-400' : 'text-white'}`}>
                {lastValue !== null ? lastValue.toFixed(2) : '—'}
              </span>
            </div>
            <div className="flex flex-col gap-1">
              <span className="text-[10px] text-slate-400 uppercase tracking-wide">Status</span>
              <div className="mt-1"><StatusBadge status={severity} /></div>
            </div>
          </div>
          {/* Progress bar per titik dalam row aktif */}
          <div className="mt-4">
            <div className="w-full bg-slate-700 rounded-full h-1.5">
              <div
                className={`h-1.5 rounded-full transition-all duration-100 ${isGangguan ? 'bg-amber-500' : 'bg-blue-500'}`}
                style={{ width: totalPointsInRow > 0 ? `${(progressInRow / totalPointsInRow) * 100}%` : '0%' }}
              />
            </div>
          </div>
        </div>

        {/* Grafik trace SOR + Pie Chart */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">

          {/* Grafik utama: trace t0..tN sebagai time-series */}
          <div className="lg:col-span-2 bg-[#1e2f50] border border-[#3b4f6e] rounded-[2.5rem] p-8">
            <div className="flex justify-between items-start mb-6">
              <div>
                <h3 className="text-sm font-black text-white tracking-widest flex items-center gap-2">
                  <div className={`w-1.5 h-4 rounded-full ${isGangguan ? 'bg-amber-500' : 'bg-blue-500'}`} />
                  SOR TRACE MONITORING
                </h3>
                <p className="text-[11px] text-slate-400 mt-1">Row {currentRowIndex + 1} — {activeLabel}</p>
                <div className="mt-3">
                  <p className="text-2xl text-white font-black leading-none">
                    {lastValue !== null ? lastValue.toFixed(2) : '—'}
                    <span className="text-sm text-slate-400 ml-1">dB</span>
                  </p>
                </div>
              </div>
              <StatusBadge status={severity} />
            </div>
            <div className="h-[320px] w-full">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={displayedPoints} margin={{ top: 10, right: 20, left: 0, bottom: 20 }}>
                  <defs>
                    <linearGradient id="colorTrace" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor={isGangguan ? '#f59e0b' : '#3b82f6'} stopOpacity={0.3} />
                      <stop offset="95%" stopColor={isGangguan ? '#f59e0b' : '#3b82f6'} stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#3d4e6b" vertical={false} />
                  <XAxis
                    dataKey="timestamp"
                    stroke="#ffffff"
                    fontSize={10}
                    tickLine={false}
                    axisLine={{ stroke: '#ffffff', strokeWidth: 1 }}
                    dy={8}
                    tick={{ fill: '#ffffff' }}
                    interval="preserveStartEnd"
                    label={{ value: 'Time', position: 'insideBottom', offset: -5, fill: '#ffffff', fontSize: 10 }}
                  />
                  <YAxis
                    stroke="#ffffff"
                    fontSize={11}
                    tickLine={false}
                    axisLine={{ stroke: '#ffffff', strokeWidth: 1 }}
                    tick={{ fill: '#ffffff' }}
                    label={{ value: 'Amplitude (dB)', angle: -90, position: 'insideLeft', fill: '#ffffff', fontSize: 11, dx: 3 }}
                  />
                  <Tooltip
                    contentStyle={{ backgroundColor: '#1e2f50', border: '1px solid #3b4f6e', borderRadius: '8px' }}
                    formatter={(v: any) => [`${Number(v).toFixed(3)} dB`, 'Amplitude']}
                    labelFormatter={(l) => `Waktu: ${l}`}
                  />
                  <Area
                    type="monotone"
                    dataKey="value"
                    stroke={isGangguan ? '#f59e0b' : '#3b82f6'}
                    strokeWidth={2}
                    fill="url(#colorTrace)"
                    dot={false}
                    isAnimationActive={false}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* Fault Distribution */}
          <div className="bg-[#1e2f50] border border-[#3b4f6e] rounded-[2.5rem] p-8 flex flex-col">
            <h3 className="text-sm font-black text-white uppercase tracking-widest mb-4 flex items-center gap-2">
              <div className="w-1.5 h-4 bg-emerald-500 rounded-full" />
              Fault Distribution
            </h3>
            <div className="flex-1 min-h-[250px]">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie data={faultDistribution} innerRadius={60} outerRadius={85} paddingAngle={5} dataKey="value" stroke="none">
                    {faultDistribution.map((_, index) => (
                      <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip contentStyle={{ backgroundColor: '#1e2f50', border: '1px solid #3b4f6e', borderRadius: '8px' }} />
                </PieChart>
              </ResponsiveContainer>
            </div>
            <div className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3">
              {faultDistribution.map((item, i) => (
                <div key={i} className="flex items-center gap-3">
                  <div className="w-3 h-3 rounded-full" style={{ backgroundColor: COLORS[i % COLORS.length] }} />
                  <span className="text-xs text-white uppercase font-bold tracking-wide">{item.name}</span>
                  <span className="text-sm text-white font-bold ml-auto">{item.value}</span>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Network Topology */}
        <section className="space-y-4">
          <div className="flex justify-between items-center px-2">
            <h3 className="text-sm font-black text-white uppercase tracking-[0.2em]">Fiber Optic Topology</h3>
            {isGangguan && (
              <div className={`flex items-center gap-2 px-3 py-1 rounded-full ${severity === 'Critical' ? 'bg-red-500/10' : 'bg-amber-500/10'}`}>
                <div className={`w-2 h-2 rounded-full animate-pulse ${severity === 'Critical' ? 'bg-red-500' : 'bg-amber-500'}`} />
                <span className={`text-[10px] font-bold ${severity === 'Critical' ? 'text-red-400' : 'text-amber-400'}`}>
                  {activeLabel.toUpperCase()} DETECTED
                </span>
              </div>
            )}
          </div>
          <NetworkTopology
            losses={[
              activeRow?.metadata?.loss_1 ?? 0,
              activeRow?.metadata?.loss_2 ?? 0,
              activeRow?.metadata?.loss_3 ?? 0,
              activeRow?.metadata?.loss_4 ?? 0,
            ]}
            prx={activeRow?.metadata?.prx ?? -14}
            klasifikasi={activeRow?.label ?? 'Normal'}
            status={severity}
            cutKM={-1}
            currentRecord={activeRow?.metadata ?? null}
          />
        </section>

        {/* Trace Log Table */}
        <div className="bg-[#1e2f50] border border-[#3b4f6e] rounded-[2.5rem] p-8 w-full shadow-2xl overflow-hidden">
          <div className="flex justify-between items-center mb-8">
            <h3 className="text-sm font-black text-white uppercase tracking-widest flex items-center gap-2">
              <div className="w-1.5 h-4 bg-indigo-500 rounded-full" />
              Trace Log
            </h3>
          </div>
          <div className="overflow-x-auto max-h-[400px] overflow-y-auto">
            <table className="w-full text-left">
              <thead className="sticky top-0 bg-[#1e2f50] z-10">
                <tr className="border-b border-[#3b4f6e] text-white text-[13px] font-black tracking-widest">
                  <th className="pb-4 px-4">ROW</th>
                  <th className="pb-4 px-4">LABEL</th>
                  <th className="pb-4 px-4 text-center">TOTAL TITIK</th>
                  <th className="pb-4 px-4 text-center">STATUS</th>
                </tr>
              </thead>
              <tbody>
                {passedRows.length > 0
                  ? [...passedRows].reverse().slice(0, 20).map(row => (
                    <tr
                      key={row.rowIndex}
                      className={`border-b border-[#3b4f6e]/50 transition-colors ${
                        row.rowIndex === currentRowIndex ? 'bg-blue-500/10' : 'hover:bg-[#2a3d60]/20'
                      }`}
                    >
                      <td className="py-4 px-4 text-slate-300 text-xs font-mono">
                        Row {row.rowIndex + 1}
                        {row.rowIndex === currentRowIndex && <span className="ml-2 text-blue-400 text-[10px]">● live</span>}
                      </td>
                      <td className="py-4 px-4">
                        <span className={`px-3 py-1 rounded-full text-[11px] font-black border ${
                          !isGangguanLabel(row.label)
                            ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30'
                            : getLabelSeverity(row.label) === 'Critical'
                              ? 'bg-red-500/20 text-red-400 border-red-500/30'
                              : 'bg-amber-500/20 text-amber-400 border-amber-500/30'
                        }`}>
                          {row.label}
                        </span>
                      </td>
                      <td className="py-4 px-4 text-center text-white text-xs font-mono">{row.tracePoints.length} titik</td>
                      <td className="py-4 px-4 text-center"><StatusBadge status={getLabelSeverity(row.label)} /></td>
                    </tr>
                  ))
                  : (
                    <tr>
                      <td colSpan={4} className="py-10 text-center text-slate-500 italic">Belum ada trace yang diputar</td>
                    </tr>
                  )}
              </tbody>
            </table>
          </div>
        </div>

      </main>
    </div>
  );
};

export default MainDashboard;
