// frontend/src/components/MainDashboard.tsx
// OTDR Monitoring Simulator - FULL RESPONSIVE + FULL WIDTH

import { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import {
  Play, Pause, Square, RotateCcw, Upload, AlertCircle,
  ChevronLeft, ChevronRight, FileSpreadsheet,
  Activity, Zap, RefreshCw, Clock
} from 'lucide-react';
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  ArcElement,
  Title,
  Tooltip,
  Legend,
  Filler,
} from 'chart.js';
import { Line, Pie } from 'react-chartjs-2';
import zoomPlugin from 'chartjs-plugin-zoom';

// Register ChartJS plugins
ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  ArcElement,
  Title,
  Tooltip,
  Legend,
  Filler,
  zoomPlugin
);

// ─────────────────────────────────────────────────────────────
// TIPE DATA
// ─────────────────────────────────────────────────────────────

type PredictionResult = {
  start: number;
  end: number;
  prediction: string;
  confidence: number;
};

type ProcessedData = {
  success: boolean;
  backscatter: number[];
  distance: number[];        // kolom Distance dari CSV — [] jika tidak ada
  predictions: PredictionResult[];
  total_windows: number;
  window_size: number;
  stride?: number;
  total_points: number;
  filename: string;
  // Verdict final dari backend (majority vote seluruh window, dihitung sekali
  // saat file diproses). Ini sumber kebenaran tunggal — sama dengan yang
  // disimpan ke DB dan yang memicu notifikasi Telegram.
  classification: string;
  status: string;
  metadata: {
    columns: string[];
    rows: number;
  };
};

type StatusType = 'idle' | 'uploading' | 'processing' | 'ready' | 'playing' | 'paused' | 'complete' | 'error';

type DashboardHistoryItem = {
  id: number;
  filename: string;
  classification: string;
  status: string;
  total_points: number;
  total_windows: number;
  created_at: string;
};

// ─────────────────────────────────────────────────────────────
// KOMPONEN PEMBANTU
// ─────────────────────────────────────────────────────────────

const StatusBadge = ({ status }: { status: string | null | undefined }) => {
  const cfg: Record<string, string> = {
    'Normal': 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
    'Warning': 'bg-amber-500/15 text-amber-400 border-amber-500/30',
    'Critical': 'bg-red-500/15 text-red-400 border-red-500/30',
    'Error': 'bg-red-500/15 text-red-400 border-red-500/30',
  };
  const dot: Record<string, string> = {
    'Normal': 'bg-emerald-400',
    'Warning': 'bg-amber-400',
    'Critical': 'bg-red-400',
    'Error': 'bg-red-400',
  };
  const s = status || 'Warning';
  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-black uppercase border ${cfg[s] || cfg.Warning}`}>
      <span className={`w-1.5 h-1.5 rounded-full animate-pulse ${dot[s] || dot.Warning}`} />
      {s}
    </span>
  );
};

// ─────────────────────────────────────────────────────────────
// KOMPONEN UTAMA
// ─────────────────────────────────────────────────────────────

type MainDashboardProps = {
  refreshTrigger?: number;
  onDataChange?: () => void;
};

const MainDashboard = ({ refreshTrigger, onDataChange }: MainDashboardProps) => {
  // ── State ──
  const [data, setData] = useState<ProcessedData | null>(null);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState<StatusType>('idle');
  const [statusMessage, setStatusMessage] = useState('Upload file to start classification');
  const [uploadProgress, setUploadProgress] = useState(0);
  const [fileName, setFileName] = useState('');
  const [errorDetail, setErrorDetail] = useState<string | null>(null);

  // Playback state
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentPointIndex, setCurrentPointIndex] = useState(0);

  // Chart refs
  const chartRef = useRef<any>(null);
  const playbackIntervalRef = useRef<NodeJS.Timeout | null>(null);

  // ── Classification History (DB) ──
  const [dbHistory, setDbHistory] = useState<DashboardHistoryItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);

  // ── Playback speed ──
  // Animasi trace dipercepat: interval lebih pendek + beberapa titik per tick,
  // sehingga visualisasi selesai jauh lebih cepat namun tetap terlihat halus.
  const PLAYBACK_INTERVAL_MS = 16;
  const POINTS_PER_TICK = 5;

  // ── Normalisasi nama kelas untuk tampilan ──
  // Label encoder mengeluarkan casing campur ('normal' huruf kecil, sisanya
  // 'Air Gap' / 'Bad Splice' / 'Bending' / 'Dirty Connector'). DB juga masih
  // menyimpan record lama dengan casing berbeda. Semua dinormalkan di sini agar
  // tampil seragam dan agar pengelompokan tidak terpecah.
  const classKey = (s: string | null | undefined) =>
    (s || 'Unknown').trim().toLowerCase();

  const formatClassName = (s: string | null | undefined) =>
    classKey(s).replace(/\b\w/g, (c) => c.toUpperCase());

  // ── API Base URL ──
  const API_URL = (import.meta as any).env?.VITE_API_URL || 'https://optim-api-ckfhb5heg3f3btgz.southeastasia-01.azurewebsites.net';

  // ── Fetch Classification History dari DB ──
  const fetchDbHistory = useCallback(async () => {
    const token = localStorage.getItem('token');
    if (!token) return;
    setHistoryLoading(true);
    try {
      const res = await fetch(`${API_URL}/api/dashboard/history`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const data = await res.json();
        setDbHistory(data);
      }
    } catch (e) {
      console.error('[DASHBOARD] fetchDbHistory error:', e);
    } finally {
      setHistoryLoading(false);
    }
  }, [API_URL]);

  // ── Load history on mount ──
  useEffect(() => {
    fetchDbHistory();
  }, [fetchDbHistory]);

  // ── Handle Upload ──
  const handleUpload = useCallback(async (file: File) => {
    if (!file) return;

    const formData = new FormData();
    formData.append('file', file);

    setLoading(true);
    setErrorDetail(null);
    setStatus('uploading');
    setStatusMessage('Uploading file...');
    setFileName(file.name);
    setUploadProgress(10);

    try {
      const token = localStorage.getItem('token');
      
      setUploadProgress(30);
      setStatusMessage('Processing data...');
      setStatus('processing');
      
      const response = await fetch(`${API_URL}/api/dashboard/process-sor`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
        },
        body: formData,
      });

      setUploadProgress(70);

      if (!response.ok) {
        let errorMsg = 'Failed to process file';
        try {
          const error = await response.json();
          errorMsg = error.detail || error.message || errorMsg;
        } catch {
          errorMsg = response.statusText || errorMsg;
        }
        throw new Error(errorMsg);
      }

      const result = await response.json();
      setUploadProgress(100);
      
      if (result.success) {
        setData(result);
        setStatus('ready');
        setStatusMessage(`Ready! ${result.total_points} data points`);
        // -1 = not started yet, chart empty until Play is pressed
        setCurrentPointIndex(-1);
        setIsPlaying(false);
        
        if (chartRef.current) {
          try {
            chartRef.current.resetZoom?.();
          } catch {
            // Ignore
          }
        }
        // Refresh history setelah upload berhasil
        fetchDbHistory();
      } else {
        throw new Error(result.message || 'Processing failed');
      }

    } catch (err: any) {
      setStatus('error');
      setStatusMessage(err.message || 'An error occurred');
      setErrorDetail(err.message || 'Unknown error');
      console.error('Upload error:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  // ── Playback Control ──
  const startPlayback = useCallback(() => {
    if (!data) return;

    // Reset if: not started (-1), already complete, or at the last data point
    if (currentPointIndex < 0 || status === 'complete' || currentPointIndex >= data.total_points - 1) {
      setCurrentPointIndex(0);
      setStatus('ready');
    }

    setIsPlaying(true);
    setStatus('playing');
    setStatusMessage('Playing trace...');
  }, [data, status, currentPointIndex]);

  const pausePlayback = useCallback(() => {
    setIsPlaying(false);
    setStatus('paused');
    setStatusMessage('Paused');
  }, []);

  const stopPlayback = useCallback(() => {
    setIsPlaying(false);
    if (playbackIntervalRef.current) {
      clearInterval(playbackIntervalRef.current);
      playbackIntervalRef.current = null;
    }
    setCurrentPointIndex(0);
    setStatus('ready');
    setStatusMessage('Reset');
    if (chartRef.current) {
      try {
        chartRef.current.resetZoom?.();
      } catch {
        // Ignore
      }
    }
  }, []);

  const resetPlayback = useCallback(() => {
    setIsPlaying(false);
    if (playbackIntervalRef.current) {
      clearInterval(playbackIntervalRef.current);
      playbackIntervalRef.current = null;
    }
    setCurrentPointIndex(0);
    setStatus('ready');
    setStatusMessage('Reset');
    if (chartRef.current) {
      try {
        chartRef.current.resetZoom?.();
      } catch {
        // Ignore
      }
    }
  }, []);

  // ── Playback Loop ──
  useEffect(() => {
    if (!isPlaying || !data) {
      if (playbackIntervalRef.current) {
        clearInterval(playbackIntervalRef.current);
        playbackIntervalRef.current = null;
      }
      return;
    }

    if (playbackIntervalRef.current) {
      clearInterval(playbackIntervalRef.current);
    }

    playbackIntervalRef.current = setInterval(() => {
      setCurrentPointIndex(prev => {
        // Maju beberapa titik sekaligus agar animasi lebih cepat
        const next = Math.min(prev + POINTS_PER_TICK, data.total_points - 1);

        if (next >= data.total_points - 1 && prev >= data.total_points - 1) {
          setIsPlaying(false);
          setStatus('complete');
          setStatusMessage('Playback complete! ✅');
          if (playbackIntervalRef.current) {
            clearInterval(playbackIntervalRef.current);
            playbackIntervalRef.current = null;
          }
          return prev;
        }

        // Playback murni animasi visual — tidak lagi mengubah hasil klasifikasi.
        // Verdict sudah ditetapkan backend saat file diproses.

        // Tandai selesai bila sudah mencapai titik terakhir
        if (next >= data.total_points - 1) {
          setIsPlaying(false);
          setStatus('complete');
          setStatusMessage('Playback complete! ✅');
          if (playbackIntervalRef.current) {
            clearInterval(playbackIntervalRef.current);
            playbackIntervalRef.current = null;
          }
        }

        return next;
      });
    }, PLAYBACK_INTERVAL_MS);

    return () => {
      if (playbackIntervalRef.current) {
        clearInterval(playbackIntervalRef.current);
        playbackIntervalRef.current = null;
      }
    };
  }, [isPlaying, data]);

  // ── Chart Data — Loss (dB) trace, sumbu X = Distance (km) ──
  const chartData = useMemo(() => {
    if (!data || currentPointIndex < 0) return null;

    const displayedData = data.backscatter.slice(0, currentPointIndex + 1);

    // Pastikan distance tersedia dan valid (non-null, panjang sama)
    const distArr = data.distance ?? [];
    const hasDistance =
      distArr.length === data.backscatter.length &&
      distArr.some(v => v !== null && v !== undefined);

    // Format {x, y}: x = Distance (km), y = Loss (dB)
    const points = displayedData.map((val, i) => {
      const d = distArr[i];
      return {
        x: hasDistance && d !== null && d !== undefined ? Number(d) : i,
        y: val !== null && val !== undefined ? Number(val) : null,
      };
    }).filter(p => p.y !== null);

    return {
      datasets: [
        {
          label: 'Loss (dB)',
          data: points,
          borderColor: '#3b82f6',
          backgroundColor: 'rgba(59, 130, 246, 0.08)',
          borderWidth: 1.5,
          pointRadius: 0,
          tension: 0.2,
          fill: true,
        },
      ],
    };
  }, [data, currentPointIndex]);

  // ── Chart Options ──
  // xMax mengikuti currentPointIndex agar sumbu X tumbuh bertahap seiring animasi
  const xMax = useMemo(() => {
    if (!data || currentPointIndex < 0) return undefined;
    const distArr = data.distance ?? [];
    const hasDistance =
      distArr.length === data.backscatter.length &&
      distArr.some(v => v !== null && v !== undefined);

    if (hasDistance) {
      const d = distArr[currentPointIndex];
      return d !== null && d !== undefined ? Number(d) : undefined;
    }
    return currentPointIndex;
  }, [data, currentPointIndex]);

  const chartOptions = useMemo(() => ({
    responsive: true,
    maintainAspectRatio: false,
    interaction: {
      mode: 'nearest' as const,
      axis: 'x' as const,
      intersect: false,
    },
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: '#1e2f50',
        titleColor: '#ffffff',
        bodyColor: '#e2e8f0',
        borderColor: '#3b4f6e',
        borderWidth: 1,
        cornerRadius: 8,
        padding: 10,
        callbacks: {
          title: function(items: any[]) {
            if (!items.length) return '';
            const x = Number(items[0].parsed.x);
            return `Distance: ${x.toFixed(4)} km`;
          },
          label: function(context: any) {
            return `Loss: ${Number(context.parsed.y).toFixed(3)} dB`;
          },
        },
      },
      zoom: {
        zoom: {
          wheel: { enabled: true, speed: 0.05 },
          pinch: { enabled: true },
          mode: 'x' as const,
        },
        pan: {
          enabled: true,
          mode: 'x' as const,
        },
        limits: {
          x: { minRange: 0.0001 },
        },
      },
    },
    scales: {
      x: {
        type: 'linear' as const,
        min: 0,
        max: xMax,   // ← X axis stops at last data point
        title: {
          display: true,
          text: 'Distance (km)',
          color: '#ffffff',
          font: { weight: 'bold' as const, size: 13 },
        },
        grid: { color: '#2a3d6080' },
        ticks: {
          color: '#ffffff',
          maxTicksLimit: 8,
          font: { size: 12 },
          callback: function(val: any) {
            const n = Number(val);
            return n.toFixed(3);
          },
        },
      },
      y: {
        title: {
          display: true,
          text: 'Loss (dB)',
          color: '#ffffff',
          font: { weight: 'bold' as const, size: 13 },
        },
        grid: { color: '#2a3d6080' },
        ticks: {
          color: '#ffffff',
          font: { size: 12 },
        },
        reverse: false,
      },
    },
  }), [xMax]);

  // ── Verdict final (tetap, tidak berubah selama animasi) ──
  // Diambil langsung dari respons backend — hasil majority vote seluruh window
  // yang dihitung sekali saat file diproses. Konsisten dengan Classification
  // History, isi DB, dan notifikasi Telegram karena sumbernya satu.
  const verdictClass = data ? formatClassName(data.classification) : null;
  const verdictStatus = data?.status || null;

  // ── Posisi Distance yang sedang diproses (mengikuti sumbu X chart) ──
  const currentDistance = useMemo(() => {
    if (!data || currentPointIndex < 0) return null;
    const distArr = data.distance ?? [];
    const hasDistance =
      distArr.length === data.backscatter.length &&
      distArr.some(v => v !== null && v !== undefined);
    if (!hasDistance) return null;
    const d = distArr[currentPointIndex];
    return d !== null && d !== undefined ? Number(d) : null;
  }, [data, currentPointIndex]);

  const progress = data ? ((currentPointIndex + 1) / data.total_points * 100) : 0;

  const getPredictionColor = (prediction: string) => {
    const p = prediction.toLowerCase();
    if (p === 'normal') return 'text-emerald-400 bg-emerald-500/20 border-emerald-500/30';
    if (p.includes('cut') || p === 'fiber cut') return 'text-red-400 bg-red-500/20 border-red-500/30';
    if (p.includes('bend')) return 'text-amber-400 bg-amber-500/20 border-amber-500/30';
    if (p.includes('splice')) return 'text-orange-400 bg-orange-500/20 border-orange-500/30';
    return 'text-blue-400 bg-blue-500/20 border-blue-500/30';
  };

  // ── Fault Distribution (agregasi dari Classification History) ──
  const CLASS_COLORS: Record<string, string> = {
    normal: '#34d399',
    'fiber cut': '#f87171',
    'nearly cut': '#fb7185',
    bending: '#fbbf24',
    'bad splice': '#fb923c',
    'dirty connector': '#22d3ee',
    'air gap': '#a78bfa',
  };

  const faultDistribution = useMemo(() => {
    // Dikelompokkan pakai key ternormalisasi, supaya record lama di DB dengan
    // casing berbeda (mis. 'bending') tidak terpisah dari 'Bending'.
    const counts = new Map<string, number>();
    dbHistory.forEach(item => {
      const key = classKey(item.classification);
      counts.set(key, (counts.get(key) || 0) + 1);
    });
    const keys = Array.from(counts.keys());
    const labels = keys.map(k => formatClassName(k));
    const values = keys.map(k => counts.get(k) || 0);
    const colors = keys.map(k => CLASS_COLORS[k] || '#60a5fa');
    const total = values.reduce((a, b) => a + b, 0);
    return { labels, values, colors, total };
  }, [dbHistory]);

  const pieData = useMemo(() => ({
    labels: faultDistribution.labels,
    datasets: [
      {
        data: faultDistribution.values,
        backgroundColor: faultDistribution.colors,
        borderColor: '#14213d',
        borderWidth: 2,
        hoverOffset: 6,
      },
    ],
  }), [faultDistribution]);

  const pieOptions = useMemo(() => ({
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: {
        position: 'bottom' as const,
        labels: {
          color: '#e2e8f0',
          font: { size: 12 },
          padding: 12,
          usePointStyle: true,
          pointStyle: 'circle' as const,
        },
      },
      tooltip: {
        backgroundColor: '#1e2f50',
        titleColor: '#ffffff',
        bodyColor: '#e2e8f0',
        borderColor: '#3b4f6e',
        borderWidth: 1,
        cornerRadius: 8,
        padding: 10,
        callbacks: {
          label: function(context: any) {
            const value = Number(context.parsed) || 0;
            const total = faultDistribution.total || 1;
            const pct = ((value / total) * 100).toFixed(1);
            return `${context.label}: ${value} (${pct}%)`;
          },
        },
      },
    },
  }), [faultDistribution]);

  // ── Render ──
  return (
    <div className="min-h-screen w-full bg-[#14213d] text-white font-sans">
      <main className="w-full px-3 sm:px-4 md:px-6 py-3 sm:py-4 md:py-6 space-y-3 sm:space-y-4 md:space-y-6">

        {/* Header - Responsive */}
        <div className="flex flex-col sm:flex-row flex-wrap justify-between items-start sm:items-center gap-2 sm:gap-3">
          <div className="w-full sm:w-auto">
            <h1 className="text-lg sm:text-xl md:text-2xl font-bold text-white flex items-center gap-2">
              <Activity className="w-5 h-5 sm:w-6 sm:h-6 text-blue-400 flex-shrink-0" />
              <span className="truncate">OTDR Monitoring Simulator</span>
            </h1>
            <p className="text-xs sm:text-sm text-white/80 truncate">
            </p>
          </div>
          <div className="flex items-center gap-2 w-full sm:w-auto">
            <label className={`flex-1 sm:flex-none px-3 sm:px-4 py-1.5 sm:py-2 rounded-xl font-medium cursor-pointer transition-colors text-xs sm:text-sm text-center ${
              loading ? 'bg-slate-600 cursor-not-allowed' : 'bg-blue-600 hover:bg-blue-700 text-white shadow-lg shadow-blue-600/25'
            }`}>
              <input
                type="file"
                accept=".xlsx,.xls,.csv"
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file) handleUpload(file);
                  e.target.value = '';
                }}
                disabled={loading}
                className="hidden"
              />
              <FileSpreadsheet size={14} className="inline mr-1 sm:mr-2" />
              {loading ? 'Uploading...' : 'Upload File'}
            </label>
            {data && (
              <span className="text-xs text-white/80 bg-[#1e2f50] px-2 sm:px-3 py-1 rounded-full border border-[#3b4f6e] truncate max-w-[100px] sm:max-w-[150px] md:max-w-[200px]">
                {data.filename}
              </span>
            )}
          </div>
        </div>

        {/* Status Bar - Responsive */}
        <div className="bg-[#1e2f50] border border-[#3b4f6e] rounded-xl p-2 sm:p-3 md:p-4 shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2 sm:gap-3 min-w-0">
              <div className={`w-2 h-2 sm:w-2.5 sm:h-2.5 rounded-full flex-shrink-0 ${
                status === 'error' ? 'bg-red-500' :
                status === 'ready' ? 'bg-green-500' :
                status === 'playing' ? 'bg-green-500 animate-pulse' :
                status === 'complete' ? 'bg-blue-500' :
                status === 'idle' ? 'bg-slate-400' :
                'bg-amber-500 animate-pulse'
              }`} />
              <span className="text-xs sm:text-xs md:text-sm font-medium text-white truncate">
                {status === 'idle' && '📂 Ready to upload'}
                {status === 'uploading' && 'Uploading...'}
                {status === 'processing' && 'Processing data...'}
                {status === 'ready' && 'Ready to play'}
                {status === 'playing' && 'Playing...'}
                {status === 'paused' && 'Paused'}
                {status === 'complete' && 'Playback complete'}
                {status === 'error' && 'Error'}
              </span>
            </div>
            {data && (
              <div className="flex items-center gap-1 sm:gap-3 text-xs sm:text-xs text-white/80 flex-wrap">
                <span className="bg-[#0f1a2e] px-1.5 sm:px-2 py-0.5 sm:py-1 rounded border border-[#3b4f6e]">Points: {data.total_points}</span>
                {currentDistance !== null && (
                  <span className="bg-[#0f1a2e] px-1.5 sm:px-2 py-0.5 sm:py-1 rounded border border-[#3b4f6e]">
                    Distance: {currentDistance.toFixed(4)} km
                  </span>
                )}
              </div>
            )}
          </div>
          {/* Progress Bar */}
          {data && (
            <div className="mt-1.5 sm:mt-2 w-full bg-slate-700 rounded-full h-1 overflow-hidden">
              <div
                className={`h-1 rounded-full transition-all duration-100 ${
                  status === 'error' ? 'bg-red-500' :
                  status === 'complete' ? 'bg-blue-500' :
                  status === 'playing' || status === 'paused' ? 'bg-green-500' :
                  'bg-blue-500'
                }`}
                style={{ width: `${Math.min(progress, 100)}%` }}
              />
            </div>
          )}
          {loading && (
            <div className="mt-1.5 sm:mt-2 w-full bg-slate-700 rounded-full h-1 overflow-hidden">
              <div
                className="h-1 rounded-full bg-blue-500 transition-all duration-300"
                style={{ width: `${uploadProgress}%` }}
              />
            </div>
          )}
          {errorDetail && status === 'error' && (
            <div className="mt-1.5 sm:mt-2 p-1.5 sm:p-2 bg-red-500/10 border border-red-500/30 rounded-lg text-xs sm:text-xs text-red-400 break-all">
              <strong>Error:</strong> {errorDetail}
            </div>
          )}
          {statusMessage && status !== 'error' && (
            <div className="mt-0.5 sm:mt-1 text-xs sm:text-xs text-white/80 truncate">
              {statusMessage}
            </div>
          )}
        </div>

        {/* Chart - Full Width Responsive */}
        <div className="bg-[#1e2f50] border border-[#3b4f6e] rounded-2xl p-2 sm:p-3 md:p-4 shadow-sm w-full">
          <div className="h-[200px] sm:h-[280px] md:h-[350px] lg:h-[400px] w-full">
            {chartData ? (
              <Line
                ref={chartRef}
                data={chartData}
                options={chartOptions}
              />
            ) : (
              <div className="h-full flex items-center justify-center text-white/60">
                <div className="text-center px-4">
                  <div className="w-12 h-12 sm:w-16 sm:h-16 md:w-20 md:h-20 mx-auto mb-2 sm:mb-3 md:mb-4 bg-[#0f1a2e] rounded-full flex items-center justify-center border border-[#3b4f6e]">
                    <Activity className="w-6 h-6 sm:w-8 sm:h-8 md:w-10 md:h-10 text-white/60" />
                  </div>
                  <p className="font-medium text-white text-sm sm:text-base">Upload file to view the classification result</p>
                  <p className="text-xs sm:text-sm text-white/80 mt-1">Format: CSV, Excel (.xlsx)</p>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Controls & Status - Responsive Grid */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4">
          {/* Controls */}
          <div className="bg-[#1e2f50] border border-[#3b4f6e] rounded-2xl p-3 sm:p-4 shadow-sm">
            <div className="grid grid-cols-4 gap-1 sm:gap-1.5">
              <button
                onClick={startPlayback}
                disabled={!data || isPlaying || status === 'complete'}
                className="px-1.5 sm:px-2 py-1.5 sm:py-2 bg-emerald-600 hover:bg-emerald-700 disabled:bg-slate-600 text-white rounded-xl font-medium transition-colors flex items-center justify-center gap-0.5 sm:gap-1 text-xs sm:text-xs md:text-sm"
              >
                <Play size={12} className="sm:w-3.5 sm:h-3.5" /> <span className="hidden xs:inline">Play</span>
              </button>
              <button
                onClick={pausePlayback}
                disabled={!isPlaying}
                className="px-1.5 sm:px-2 py-1.5 sm:py-2 bg-amber-600 hover:bg-amber-700 disabled:bg-slate-600 text-white rounded-xl font-medium transition-colors flex items-center justify-center gap-0.5 sm:gap-1 text-xs sm:text-xs md:text-sm"
              >
                <Pause size={12} className="sm:w-3.5 sm:h-3.5" /> <span className="hidden xs:inline">Pause</span>
              </button>
              <button
                onClick={stopPlayback}
                disabled={!data || currentPointIndex === 0}
                className="px-1.5 sm:px-2 py-1.5 sm:py-2 bg-red-600 hover:bg-red-700 disabled:bg-slate-600 text-white rounded-xl font-medium transition-colors flex items-center justify-center gap-0.5 sm:gap-1 text-xs sm:text-xs md:text-sm"
              >
                <Square size={12} className="sm:w-3.5 sm:h-3.5" /> <span className="hidden xs:inline">Stop</span>
              </button>
              <button
                onClick={resetPlayback}
                disabled={!data}
                className="px-1.5 sm:px-2 py-1.5 sm:py-2 bg-slate-600 hover:bg-slate-700 disabled:bg-slate-500 text-white rounded-xl font-medium transition-colors flex items-center justify-center gap-0.5 sm:gap-1 text-xs sm:text-xs md:text-sm"
              >
                <RotateCcw size={12} className="sm:w-3.5 sm:h-3.5" /> <span className="hidden xs:inline">Reset</span>
              </button>
            </div>
            <div className="mt-1.5 sm:mt-2 text-xs sm:text-xs text-white/80 text-center truncate">
              {data ? (
                <>
                  Point {Math.min(currentPointIndex + 1, data.total_points)} / {data.total_points}
                  {currentDistance !== null && (
                    <> · Distance {currentDistance.toFixed(4)} km</>
                  )}
                </>
              ) : (
                'No data available'
              )}
            </div>
          </div>

          {/* Classification Result — verdict tetap dari backend */}
          <div className="bg-[#1e2f50] border border-[#3b4f6e] rounded-2xl p-3 sm:p-4 shadow-sm">
            <h3 className="text-xs sm:text-xs font-bold text-white/80 uppercase tracking-wider mb-1 sm:mb-2">Classification Result</h3>
            {verdictClass ? (
              <div className="flex items-center gap-2 sm:gap-3 flex-wrap">
                <span className={`px-3 sm:px-4 py-1 sm:py-1.5 rounded-full text-sm sm:text-base font-bold border ${getPredictionColor(verdictClass)}`}>
                  {verdictClass}
                </span>
              </div>
            ) : (
              <div className="text-xs sm:text-sm text-white/80">
                Upload a file to see the result
              </div>
            )}
          </div>

          {/* Status — mengikuti verdict backend */}
          <div className="bg-[#1e2f50] border border-[#3b4f6e] rounded-2xl p-3 sm:p-4 shadow-sm">
            <h3 className="text-xs sm:text-xs font-bold text-white/80 uppercase tracking-wider mb-1 sm:mb-2">Status</h3>
            {verdictStatus ? (
              <StatusBadge status={verdictStatus} />
            ) : (
              <div className="flex items-center gap-1.5 sm:gap-2">
                <div className={`w-2 h-2 sm:w-2.5 sm:h-2.5 rounded-full flex-shrink-0 ${
                  status === 'error' ? 'bg-red-500' : 'bg-slate-400'
                }`} />
                <span className="font-medium text-xs sm:text-sm text-white">
                  {status === 'error' ? 'Error' : 'Idle'}
                </span>
              </div>
            )}
            <div className="text-xs sm:text-xs text-white/80 mt-1.5">
              {currentDistance !== null ? `Distance: ${currentDistance.toFixed(4)} km` : 'Distance: -'}
            </div>
          </div>

          {/* Zoom Controls */}
          <div className="bg-[#1e2f50] border border-[#3b4f6e] rounded-2xl p-3 sm:p-4 shadow-sm">
            <h3 className="text-xs sm:text-xs font-bold text-white/80 uppercase tracking-wider mb-1 sm:mb-2">Zoom</h3>
            <div className="flex gap-1.5 sm:gap-2">
              <button
                onClick={() => {
                  if (chartRef.current) {
                    try {
                      chartRef.current.zoom?.(-1);
                    } catch {
                      // Ignore
                    }
                  }
                }}
                className="flex-1 px-2 sm:px-3 py-1.5 sm:py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-xl text-xs sm:text-sm font-medium transition-colors flex items-center justify-center gap-0.5 sm:gap-1"
              >
                <ChevronLeft size={12} className="sm:w-3.5 sm:h-3.5" /> Zoom In
              </button>
              <button
                onClick={() => {
                  if (chartRef.current) {
                    try {
                      chartRef.current.zoom?.(0.8);
                    } catch {
                      // Ignore
                    }
                  }
                }}
                className="flex-1 px-2 sm:px-3 py-1.5 sm:py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-xl text-xs sm:text-sm font-medium transition-colors flex items-center justify-center gap-0.5 sm:gap-1"
              >
                Zoom Out <ChevronRight size={12} className="sm:w-3.5 sm:h-3.5" />
              </button>
            </div>
          </div>
        </div>

        {/* Info Panel */}
        {data && (
          <div className="bg-blue-500/10 border border-blue-500/20 rounded-2xl p-3 sm:p-4 text-xs sm:text-sm text-blue-400">
            <div className="flex items-start gap-2 sm:gap-3">
              <Zap className="w-4 h-4 sm:w-5 sm:h-5 text-blue-400 flex-shrink-0 mt-0.5" />
              <div className="w-full overflow-hidden">
                <p className="font-medium text-white text-xs sm:text-sm">Data Information</p>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-1 sm:gap-2 mt-1 text-xs sm:text-xs text-white">
                  <span className="truncate">Points: <strong className="text-white">{data.total_points}</strong></span>
                  <span className="truncate">File: <strong className="text-white truncate">{data.filename}</strong></span>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* ── Classification History + Fault Distribution (2 kolom) ── */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 sm:gap-4 md:gap-6 items-stretch">

        {/* Kiri: Classification History */}
        <div className="bg-[#1a2a45] border border-[#2a3d60] rounded-2xl overflow-hidden flex flex-col">
          <div className="flex items-center justify-between px-4 py-3 border-b border-[#2a3d60]">
            <div className="flex items-center gap-2">
              <span className="w-1 h-4 bg-blue-400 rounded-full" />
              <span className="text-white text-sm font-semibold">Classification History</span>
              <span className="text-white/80 text-xs">({dbHistory.length} entries)</span>
            </div>
            <button
              onClick={fetchDbHistory}
              className="text-white/80 hover:text-white transition-colors"
              title="Refresh"
            >
              <RefreshCw className="w-3.5 h-3.5" />
            </button>
          </div>
          <div className="flex-1 overflow-x-auto max-h-96 overflow-y-auto">
            <table className="w-full text-xs sm:text-sm">
              <thead className="bg-[#0f1e35] text-white/80 uppercase tracking-wide sticky top-0">
                <tr>
                  <th className="px-3 py-2 text-left w-10">No</th>
                  <th className="px-3 py-2 text-left">Time</th>
                  <th className="px-3 py-2 text-left">Classification Result</th>
                  <th className="px-3 py-2 text-left">Status</th>
                </tr>
              </thead>
              <tbody>
                {historyLoading ? (
                  <tr>
                    <td colSpan={4} className="px-3 py-6 text-center text-white/60">Loading...</td>
                  </tr>
                ) : dbHistory.length === 0 ? (
                  <tr>
                    <td colSpan={4} className="px-3 py-8 text-center text-white/60">
                      No classification history yet.
                    </td>
                  </tr>
                ) : (
                  dbHistory.map((item, idx) => {
                    const cls = classKey(item.classification);

                    const clsBadge =
                      cls === 'normal' ? 'bg-emerald-500/20 text-emerald-400' :
                      cls.includes('cut') || cls.includes('nearly') ? 'bg-red-500/20 text-red-400' :
                      cls.includes('bend') ? 'bg-amber-500/20 text-amber-400' :
                      cls.includes('air') || cls.includes('gap') ? 'bg-purple-500/20 text-purple-400' :
                      cls.includes('splice') ? 'bg-orange-500/20 text-orange-400' :
                      cls.includes('dirty') || cls.includes('connector') ? 'bg-cyan-500/20 text-cyan-400' :
                      'bg-blue-500/20 text-blue-400';

                    const statusBadge: Record<string, string> = {
                      Normal:   'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30',
                      Warning:  'bg-amber-500/20  text-amber-400  border border-amber-500/30',
                      Critical: 'bg-red-500/20    text-red-400    border border-red-500/30',
                    };
                    const statusDot: Record<string, string> = {
                      Normal:   'bg-emerald-400',
                      Warning:  'bg-amber-400',
                      Critical: 'bg-red-400',
                    };
                    const itemStatus = item.status as 'Normal' | 'Warning' | 'Critical';

                    const formatDate = (iso: string) => {
                      const d = new Date(iso);
                      return d.toLocaleDateString('id-ID', {
                        day: '2-digit', month: 'short', year: 'numeric',
                      }) + ' ' + d.toLocaleTimeString('id-ID', {
                        hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
                      });
                    };

                    return (
                      <tr key={item.id} className="border-t border-[#2a3d60]/50 hover:bg-[#2a3d60]/20">
                        <td className="px-3 py-2 text-white/80 font-mono">{idx + 1}</td>
                        <td className="px-3 py-2 text-white/80 font-mono whitespace-nowrap">
                          {item.created_at ? formatDate(item.created_at) : '-'}
                        </td>
                        <td className="px-3 py-2">
                          <span className={`px-2 py-0.5 rounded-full text-xs font-semibold ${clsBadge}`}>
                            {formatClassName(item.classification)}
                          </span>
                        </td>
                        <td className="px-3 py-2">
                          <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-semibold ${statusBadge[itemStatus] || statusBadge.Warning}`}>
                            <span className={`w-1.5 h-1.5 rounded-full ${statusDot[itemStatus] || statusDot.Warning}`} />
                            {itemStatus}
                          </span>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* Kanan: Fault Distribution */}
        <div className="bg-[#1a2a45] border border-[#2a3d60] rounded-2xl overflow-hidden flex flex-col">
          <div className="flex items-center gap-2 px-4 py-3 border-b border-[#2a3d60]">
            <span className="w-1 h-4 bg-blue-400 rounded-full" />
            <span className="text-white text-sm font-semibold">Fault Distribution</span>
            <span className="text-white/80 text-xs">({faultDistribution.total} entries)</span>
          </div>
          <div className="flex-1 flex items-center justify-center p-4 min-h-[300px]">
            {faultDistribution.total > 0 ? (
              <div className="w-full h-full max-w-[420px] mx-auto">
                <Pie data={pieData} options={pieOptions} />
              </div>
            ) : (
              <div className="text-center text-white/60 text-sm py-8">
                No classification data yet.
              </div>
            )}
          </div>
        </div>

        </div>

      </main>
    </div>
  );
};

export default MainDashboard;