// frontend/src/components/MainDashboard.tsx
// OTDR Monitoring Simulator - FULL RESPONSIVE + FULL WIDTH

import { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import {
  Play, Pause, Square, RotateCcw, Upload, AlertCircle,
  ChevronLeft, ChevronRight, Maximize2, FileSpreadsheet,
  Activity, Zap, RefreshCw, Clock, TrendingUp
} from 'lucide-react';
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
  Filler,
} from 'chart.js';
import { Line } from 'react-chartjs-2';
import zoomPlugin from 'chartjs-plugin-zoom';

// Register ChartJS plugins
ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
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
  distance: number[];        // kolom Distance (m) dari CSV — [] jika tidak ada
  predictions: PredictionResult[];
  total_windows: number;
  window_size: number;
  total_points: number;
  filename: string;
  metadata: {
    columns: string[];
    rows: number;
  };
};

type HistoryEntry = {
  time: string;
  window: string;
  prediction: string;
  confidence: number;
};

type StatusType = 'idle' | 'uploading' | 'processing' | 'ready' | 'playing' | 'paused' | 'complete' | 'error';

type DashboardHistoryItem = {
  id: number;
  filename: string;
  dominant_class: string;
  dominant_percentage: number;
  total_points: number;
  total_windows: number;
  prediction_summary: Record<string, number> | null;
  created_at: string;
};

type DashboardStats = {
  total_files: number;
  total_predictions: number;
  class_distribution: Record<string, number>;
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
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-black uppercase border ${cfg[s] || cfg.Warning}`}>
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
  const [statusMessage, setStatusMessage] = useState('Upload file SOR untuk memulai monitoring');
  const [uploadProgress, setUploadProgress] = useState(0);
  const [fileName, setFileName] = useState('');
  const [errorDetail, setErrorDetail] = useState<string | null>(null);

  // Playback state
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentPointIndex, setCurrentPointIndex] = useState(0);
  const [currentPredictionIndex, setCurrentPredictionIndex] = useState(-1);
  const [history, setHistory] = useState<HistoryEntry[]>([]);

  // Chart refs
  const chartRef = useRef<any>(null);
  const playbackIntervalRef = useRef<NodeJS.Timeout | null>(null);

  // ── Classification History (DB) ──
  const [dbHistory, setDbHistory] = useState<DashboardHistoryItem[]>([]);
  const [dbStats, setDbStats] = useState<DashboardStats | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);

  // ── Playback speed ──
  const PLAYBACK_INTERVAL_MS = 30;

  // ── Format waktu ──
  const formatTime = (index: number) => {
    const totalMs = index * PLAYBACK_INTERVAL_MS;
    const totalSec = Math.floor(totalMs / 1000);
    const hours = String(Math.floor(totalSec / 3600)).padStart(2, '0');
    const minutes = String(Math.floor((totalSec % 3600) / 60)).padStart(2, '0');
    const seconds = String(totalSec % 60).padStart(2, '0');
    return `${hours}:${minutes}:${seconds}`;
  };

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

  // ── Fetch Statistics untuk Pie Chart ──
  const fetchStats = useCallback(async () => {
    const token = localStorage.getItem('token');
    if (!token) return;
    try {
      const res = await fetch(`${API_URL}/api/dashboard/statistics`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const data = await res.json();
        setDbStats(data);
      }
    } catch (e) {
      console.error('[DASHBOARD] fetchStats error:', e);
    }
  }, [API_URL]);

  // ── Delete History ──
  const handleDeleteHistory = useCallback(async (id: number) => {
    const token = localStorage.getItem('token');
    if (!token) return;
    if (!window.confirm('Hapus history ini?')) return;
    try {
      const res = await fetch(`${API_URL}/api/dashboard/history/${id}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        setDbHistory(prev => prev.filter(h => h.id !== id));
        fetchStats();
      }
    } catch (e) {
      console.error('[DASHBOARD] deleteHistory error:', e);
    }
  }, [API_URL, fetchStats]);

  // ── Load history & stats on mount ──
  useEffect(() => {
    fetchDbHistory();
    fetchStats();
  }, [fetchDbHistory, fetchStats]);

  // ── Handle Upload ──
  const handleUpload = useCallback(async (file: File) => {
    if (!file) return;

    const formData = new FormData();
    formData.append('file', file);

    setLoading(true);
    setErrorDetail(null);
    setStatus('uploading');
    setStatusMessage('Mengupload file...');
    setFileName(file.name);
    setUploadProgress(10);

    try {
      const token = localStorage.getItem('token');
      
      setUploadProgress(30);
      setStatusMessage('Memproses data...');
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
        let errorMsg = 'Gagal memproses file';
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
        setStatusMessage(`Siap diputar! ${result.total_points} titik data, ${result.total_windows} window`);
        // -1 = belum mulai diputar, grafik kosong sampai Play ditekan
        setCurrentPointIndex(-1);
        setCurrentPredictionIndex(-1);
        setHistory([]);
        setIsPlaying(false);
        
        if (chartRef.current) {
          try {
            chartRef.current.resetZoom?.();
          } catch {
            // Ignore
          }
        }
        // Refresh history & stats setelah upload berhasil
        fetchDbHistory();
        fetchStats();
      } else {
        throw new Error(result.message || 'Proses gagal');
      }

    } catch (err: any) {
      setStatus('error');
      setStatusMessage(err.message || 'Terjadi kesalahan');
      setErrorDetail(err.message || 'Unknown error');
      console.error('Upload error:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  // ── Playback Control ──
  const startPlayback = useCallback(() => {
    if (!data) return;

    // Reset jika: belum mulai (-1), sudah selesai, atau sudah di titik akhir
    if (currentPointIndex < 0 || status === 'complete' || currentPointIndex >= data.total_points - 1) {
      setCurrentPointIndex(0);
      setCurrentPredictionIndex(-1);
      setHistory([]);
      setStatus('ready');
    }

    setIsPlaying(true);
    setStatus('playing');
    setStatusMessage('Memutar trace...');
  }, [data, status, currentPointIndex]);

  const pausePlayback = useCallback(() => {
    setIsPlaying(false);
    setStatus('paused');
    setStatusMessage('Dijeda');
  }, []);

  const stopPlayback = useCallback(() => {
    setIsPlaying(false);
    if (playbackIntervalRef.current) {
      clearInterval(playbackIntervalRef.current);
      playbackIntervalRef.current = null;
    }
    setCurrentPointIndex(0);
    setCurrentPredictionIndex(-1);
    setHistory([]);
    setStatus('ready');
    setStatusMessage('Direset');
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
    setCurrentPredictionIndex(-1);
    setHistory([]);
    setStatus('ready');
    setStatusMessage('Direset');
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
        const next = prev + 1;
        
        if (next >= data.total_points) {
          setIsPlaying(false);
          setStatus('complete');
          setStatusMessage('Playback selesai! ✅');
          if (playbackIntervalRef.current) {
            clearInterval(playbackIntervalRef.current);
            playbackIntervalRef.current = null;
          }
          return prev;
        }

        const predIndex = next - data.window_size;
        if (predIndex >= 0 && predIndex < data.predictions.length) {
          setCurrentPredictionIndex(predIndex);
          
          const pred = data.predictions[predIndex];
          setHistory(h => {
            const newEntry: HistoryEntry = {
              time: formatTime(next),
              window: `${pred.start}-${pred.end}`,
              prediction: pred.prediction,
              confidence: pred.confidence,
            };
            if (h.length > 0 && h[h.length - 1].time === newEntry.time) {
              return h;
            }
            return [...h, newEntry];
          });
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

  // ── Chart Data — Backscatter trace, sumbu X = Distance (linear scale) ──
  const chartData = useMemo(() => {
    if (!data || currentPointIndex < 0) return null;

    const displayedData = data.backscatter.slice(0, currentPointIndex + 1);

    // Pastikan distance tersedia dan valid (non-null, panjang sama)
    const distArr = data.distance ?? [];
    const hasDistance =
      distArr.length === data.backscatter.length &&
      distArr.some(v => v !== null && v !== undefined);

    // Format {x, y}: x = Distance (m), y = Backscatter (dB)
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
          label: 'Backscatter (dB)',
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
            return `Distance: ${x.toFixed(4)} m`;
          },
          label: function(context: any) {
            return `Backscatter: ${Number(context.parsed.y).toFixed(3)} dB`;
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
        max: xMax,   // ← sumbu X berhenti tepat di titik terakhir data
        title: {
          display: true,
          text: 'Distance (m)',
          color: '#94a3b8',
          font: { weight: 'bold' as const, size: 12 },
        },
        grid: { color: '#2a3d60' },
        ticks: {
          color: '#94a3b8',
          maxTicksLimit: 8,
          callback: function(val: any) {
            const n = Number(val);
            return n < 1 ? n.toFixed(4) : n.toFixed(3);
          },
        },
      },
      y: {
        title: {
          display: true,
          text: 'Backscatter (dB)',
          color: '#94a3b8',
          font: { weight: 'bold' as const, size: 12 },
        },
        grid: { color: '#2a3d60' },
        ticks: { color: '#94a3b8' },
        reverse: true,
      },
    },
  }), [xMax]);

  // ── Current Prediction Info ──
  const currentPrediction = useMemo(() => {
    if (currentPredictionIndex < 0 || !data) return null;
    if (currentPredictionIndex >= data.predictions.length) return null;
    return data.predictions[currentPredictionIndex];
  }, [currentPredictionIndex, data]);

  const progress = data ? ((currentPointIndex + 1) / data.total_points * 100) : 0;

  const getPredictionColor = (prediction: string) => {
    const p = prediction.toLowerCase();
    if (p === 'normal') return 'text-emerald-400 bg-emerald-500/20 border-emerald-500/30';
    if (p.includes('cut') || p === 'fiber cut') return 'text-red-400 bg-red-500/20 border-red-500/30';
    if (p.includes('bend')) return 'text-amber-400 bg-amber-500/20 border-amber-500/30';
    if (p.includes('splice')) return 'text-orange-400 bg-orange-500/20 border-orange-500/30';
    return 'text-blue-400 bg-blue-500/20 border-blue-500/30';
  };

  // ── Render ──
  return (
    <div className="min-h-screen w-full bg-[#14213d] text-slate-300 font-sans">
      <main className="w-full px-3 sm:px-4 md:px-6 py-3 sm:py-4 md:py-6 space-y-3 sm:space-y-4 md:space-y-6">

        {/* Header - Responsive */}
        <div className="flex flex-col sm:flex-row flex-wrap justify-between items-start sm:items-center gap-2 sm:gap-3">
          <div className="w-full sm:w-auto">
            <h1 className="text-lg sm:text-xl md:text-2xl font-bold text-white flex items-center gap-2">
              <Activity className="w-5 h-5 sm:w-6 sm:h-6 text-blue-400 flex-shrink-0" />
              <span className="truncate">OTDR Monitoring Simulator</span>
            </h1>
            <p className="text-xs sm:text-sm text-slate-400 truncate">
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
              <span className="text-xs text-slate-400 bg-[#1e2f50] px-2 sm:px-3 py-1 rounded-full border border-[#3b4f6e] truncate max-w-[100px] sm:max-w-[150px] md:max-w-[200px]">
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
              <span className="text-[10px] sm:text-xs md:text-sm font-medium text-white truncate">
                {status === 'idle' && '📂 Siap upload file SOR'}
                {status === 'uploading' && '📤 Mengupload...'}
                {status === 'processing' && '⚙️ Memproses data...'}
                {status === 'ready' && '✅ Siap diputar'}
                {status === 'playing' && '▶️ Memutar...'}
                {status === 'paused' && '⏸️ Dijeda'}
                {status === 'complete' && '✅ Playback selesai'}
                {status === 'error' && '❌ Error'}
              </span>
            </div>
            {data && (
              <div className="flex items-center gap-1 sm:gap-3 text-[10px] sm:text-xs text-slate-400 flex-wrap">
                <span className="bg-[#0f1a2e] px-1.5 sm:px-2 py-0.5 sm:py-1 rounded border border-[#3b4f6e]">Titik: {data.total_points}</span>
                <span className="bg-[#0f1a2e] px-1.5 sm:px-2 py-0.5 sm:py-1 rounded border border-[#3b4f6e]">Window: {data.total_windows}</span>
                <span className="bg-[#0f1a2e] px-1.5 sm:px-2 py-0.5 sm:py-1 rounded border border-[#3b4f6e] hidden sm:inline">Size: {data.window_size}</span>
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
            <div className="mt-1.5 sm:mt-2 p-1.5 sm:p-2 bg-red-500/10 border border-red-500/30 rounded-lg text-[10px] sm:text-xs text-red-400 break-all">
              <strong>Error:</strong> {errorDetail}
            </div>
          )}
          {statusMessage && status !== 'error' && (
            <div className="mt-0.5 sm:mt-1 text-[9px] sm:text-xs text-slate-400 truncate">
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
              <div className="h-full flex items-center justify-center text-slate-500">
                <div className="text-center px-4">
                  <div className="w-12 h-12 sm:w-16 sm:h-16 md:w-20 md:h-20 mx-auto mb-2 sm:mb-3 md:mb-4 bg-[#0f1a2e] rounded-full flex items-center justify-center border border-[#3b4f6e]">
                    <Activity className="w-6 h-6 sm:w-8 sm:h-8 md:w-10 md:h-10 text-slate-500" />
                  </div>
                  <p className="font-medium text-white text-sm sm:text-base">Upload file SOR untuk memulai monitoring</p>
                  <p className="text-xs sm:text-sm text-slate-400 mt-1">Format: CSV, Excel (.xlsx) dengan kolom Backscatter</p>
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
                className="px-1.5 sm:px-2 py-1.5 sm:py-2 bg-emerald-600 hover:bg-emerald-700 disabled:bg-slate-600 text-white rounded-xl font-medium transition-colors flex items-center justify-center gap-0.5 sm:gap-1 text-[10px] sm:text-xs md:text-sm"
              >
                <Play size={12} className="sm:w-3.5 sm:h-3.5" /> <span className="hidden xs:inline">Play</span>
              </button>
              <button
                onClick={pausePlayback}
                disabled={!isPlaying}
                className="px-1.5 sm:px-2 py-1.5 sm:py-2 bg-amber-600 hover:bg-amber-700 disabled:bg-slate-600 text-white rounded-xl font-medium transition-colors flex items-center justify-center gap-0.5 sm:gap-1 text-[10px] sm:text-xs md:text-sm"
              >
                <Pause size={12} className="sm:w-3.5 sm:h-3.5" /> <span className="hidden xs:inline">Pause</span>
              </button>
              <button
                onClick={stopPlayback}
                disabled={!data || currentPointIndex === 0}
                className="px-1.5 sm:px-2 py-1.5 sm:py-2 bg-red-600 hover:bg-red-700 disabled:bg-slate-600 text-white rounded-xl font-medium transition-colors flex items-center justify-center gap-0.5 sm:gap-1 text-[10px] sm:text-xs md:text-sm"
              >
                <Square size={12} className="sm:w-3.5 sm:h-3.5" /> <span className="hidden xs:inline">Stop</span>
              </button>
              <button
                onClick={resetPlayback}
                disabled={!data}
                className="px-1.5 sm:px-2 py-1.5 sm:py-2 bg-slate-600 hover:bg-slate-700 disabled:bg-slate-500 text-white rounded-xl font-medium transition-colors flex items-center justify-center gap-0.5 sm:gap-1 text-[10px] sm:text-xs md:text-sm"
              >
                <RotateCcw size={12} className="sm:w-3.5 sm:h-3.5" /> <span className="hidden xs:inline">Reset</span>
              </button>
            </div>
            <div className="mt-1.5 sm:mt-2 text-[10px] sm:text-xs text-slate-400 text-center truncate">
              {data ? (
                <>
                  Titik {Math.min(currentPointIndex + 1, data.total_points)} / {data.total_points}
                  {currentPredictionIndex >= 0 && (
                    <> · Win {currentPredictionIndex + 1} / {data.total_windows}</>
                  )}
                </>
              ) : (
                'Belum ada data'
              )}
            </div>
          </div>

          {/* Current Prediction */}
          <div className="bg-[#1e2f50] border border-[#3b4f6e] rounded-2xl p-3 sm:p-4 shadow-sm">
            <h3 className="text-[8px] sm:text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1 sm:mb-2">Current Prediction</h3>
            {currentPrediction ? (
              <div>
                <div className="flex items-center gap-2 sm:gap-3 flex-wrap">
                  <span className={`px-2 sm:px-3 py-0.5 sm:py-1 rounded-full text-[10px] sm:text-sm font-bold border ${getPredictionColor(currentPrediction.prediction)}`}>
                    {currentPrediction.prediction}
                  </span>
                  <span className="text-base sm:text-lg font-bold text-blue-400">
                    {currentPrediction.confidence}%
                  </span>
                </div>
                <div className="text-[10px] sm:text-xs text-slate-400 mt-0.5 sm:mt-1 font-mono truncate">
                  Window: {currentPrediction.start} - {currentPrediction.end}
                </div>
              </div>
            ) : (
              <div className="text-xs sm:text-sm text-slate-400">
                {currentPointIndex >= (data?.window_size || 128) ? 'Tidak ada prediksi' : '⏳ Menunggu window...'}
              </div>
            )}
          </div>

          {/* Status */}
          <div className="bg-[#1e2f50] border border-[#3b4f6e] rounded-2xl p-3 sm:p-4 shadow-sm">
            <h3 className="text-[8px] sm:text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1 sm:mb-2">Status</h3>
            <div className="flex items-center gap-1.5 sm:gap-2">
              <div className={`w-2 h-2 sm:w-2.5 sm:h-2.5 rounded-full flex-shrink-0 ${
                status === 'playing' ? 'bg-green-500 animate-pulse' :
                status === 'complete' ? 'bg-blue-500' :
                status === 'ready' ? 'bg-green-500' :
                status === 'paused' ? 'bg-amber-500' :
                status === 'error' ? 'bg-red-500' :
                'bg-slate-400'
              }`} />
              <span className="font-medium text-xs sm:text-sm text-white">
                {status === 'playing' ? 'Playing...' :
                 status === 'complete' ? 'Complete ✅' :
                 status === 'ready' ? 'Ready' :
                 status === 'paused' ? 'Paused' :
                 status === 'error' ? 'Error' :
                 'Idle'}
              </span>
            </div>
            <div className="text-[10px] sm:text-xs text-slate-400 mt-0.5 sm:mt-1">
              Total Windows: {data?.total_windows || 0}
            </div>
          </div>

          {/* Zoom Controls */}
          <div className="bg-[#1e2f50] border border-[#3b4f6e] rounded-2xl p-3 sm:p-4 shadow-sm">
            <h3 className="text-[8px] sm:text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1 sm:mb-2">Zoom</h3>
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
                className="flex-1 px-2 sm:px-3 py-1.5 sm:py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-xl text-[10px] sm:text-sm font-medium transition-colors flex items-center justify-center gap-0.5 sm:gap-1"
              >
                <ChevronLeft size={12} className="sm:w-3.5 sm:h-3.5" /> Zoom In
              </button>
              <button
                onClick={() => {
                  if (chartRef.current) {
                    try {
                      chartRef.current.resetZoom?.();
                    } catch {
                      // Ignore
                    }
                  }
                }}
                className="flex-1 px-2 sm:px-3 py-1.5 sm:py-2 bg-slate-600 hover:bg-slate-700 text-white rounded-xl text-[10px] sm:text-sm font-medium transition-colors flex items-center justify-center gap-0.5 sm:gap-1"
              >
                <Maximize2 size={12} className="sm:w-3.5 sm:h-3.5" /> Reset
              </button>
            </div>
          </div>
        </div>

        {/* Prediction History - Responsive Table */}
        <div className="bg-[#1e2f50] border border-[#3b4f6e] rounded-2xl p-3 sm:p-4 shadow-sm w-full overflow-hidden">
          <h3 className="text-xs sm:text-sm font-bold text-white mb-2 sm:mb-3 flex items-center gap-2">
            <span className="w-1 h-4 sm:w-1.5 sm:h-5 bg-blue-500 rounded-full" />
            Prediction History
            {history.length > 0 && (
              <span className="text-[10px] sm:text-xs font-normal text-slate-400 ml-1 sm:ml-2">
                ({history.length} prediksi)
              </span>
            )}
          </h3>
          <div className="max-h-[150px] sm:max-h-[200px] overflow-y-auto">
            <div className="overflow-x-auto">
              <table className="w-full text-xs sm:text-sm">
                <thead className="bg-[#0f1a2e] sticky top-0">
                  <tr className="text-slate-400 font-medium text-[9px] sm:text-xs border-b border-[#3b4f6e]">
                    <th className="px-1.5 sm:px-3 py-1.5 sm:py-2 text-left">Time</th>
                    <th className="px-1.5 sm:px-3 py-1.5 sm:py-2 text-left hidden xs:table-cell">Window</th>
                    <th className="px-1.5 sm:px-3 py-1.5 sm:py-2 text-left">Prediction</th>
                    <th className="px-1.5 sm:px-3 py-1.5 sm:py-2 text-left">Conf.</th>
                  </tr>
                </thead>
                <tbody>
                  {history.length === 0 ? (
                    <tr>
                      <td colSpan={4} className="px-1.5 sm:px-3 py-4 sm:py-6 text-center text-slate-500 text-[10px] sm:text-sm">
                        Belum ada prediksi. Jalankan trace untuk melihat history.
                      </td>
                    </tr>
                  ) : (
                    history.slice(-50).reverse().map((entry, i) => (
                      <tr key={i} className="border-t border-[#3b4f6e]/50 hover:bg-[#2a3d60]/20">
                        <td className="px-1.5 sm:px-3 py-1 font-mono text-[9px] sm:text-xs text-slate-300 whitespace-nowrap">{entry.time}</td>
                        <td className="px-1.5 sm:px-3 py-1 font-mono text-[9px] sm:text-xs text-slate-300 hidden xs:table-cell">{entry.window}</td>
                        <td className="px-1.5 sm:px-3 py-1">
                          <span className={`px-1.5 sm:px-2 py-0.5 rounded-full text-[8px] sm:text-xs font-medium whitespace-nowrap ${
                            entry.prediction.toLowerCase() === 'normal' ? 'bg-emerald-500/20 text-emerald-400' :
                            entry.prediction.toLowerCase().includes('cut') ? 'bg-red-500/20 text-red-400' :
                            entry.prediction.toLowerCase().includes('bend') ? 'bg-amber-500/20 text-amber-400' :
                            'bg-blue-500/20 text-blue-400'
                          }`}>
                            {entry.prediction}
                          </span>
                        </td>
                        <td className="px-1.5 sm:px-3 py-1 font-mono text-[9px] sm:text-xs text-blue-400 font-medium">{entry.confidence}%</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        {/* Info Panel */}
        {data && (
          <div className="bg-blue-500/10 border border-blue-500/20 rounded-2xl p-3 sm:p-4 text-xs sm:text-sm text-blue-400">
            <div className="flex items-start gap-2 sm:gap-3">
              <Zap className="w-4 h-4 sm:w-5 sm:h-5 text-blue-400 flex-shrink-0 mt-0.5" />
              <div className="w-full overflow-hidden">
                <p className="font-medium text-white text-xs sm:text-sm">Informasi Data</p>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-1 sm:gap-2 mt-1 text-[9px] sm:text-xs text-slate-300">
                  <span className="truncate">Titik: <strong className="text-white">{data.total_points}</strong></span>
                  <span className="truncate">Window: <strong className="text-white">{data.total_windows}</strong></span>
                  <span className="truncate hidden xs:inline">Size: <strong className="text-white">{data.window_size}</strong></span>
                  <span className="truncate col-span-2 sm:col-span-1">File: <strong className="text-white truncate">{data.filename}</strong></span>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* ── Classification History + Fault Distribution ── */}
        <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">

          {/* Classification History Table — 3/5 lebar */}
          <div className="lg:col-span-3 bg-[#1a2a45] border border-[#2a3d60] rounded-2xl overflow-hidden">
            <div className="flex items-center justify-between px-4 py-3 border-b border-[#2a3d60]">
              <div className="flex items-center gap-2">
                <span className="w-1 h-4 bg-blue-400 rounded-full" />
                <span className="text-white text-sm font-semibold">Classification History</span>
                <span className="text-slate-400 text-xs">({dbHistory.length} entri)</span>
              </div>
              <button
                onClick={() => { fetchDbHistory(); fetchStats(); }}
                className="text-slate-400 hover:text-white transition-colors"
                title="Refresh"
              >
                <RefreshCw className="w-3.5 h-3.5" />
              </button>
            </div>
            <div className="overflow-x-auto max-h-72 overflow-y-auto">
              <table className="w-full text-[10px] sm:text-xs">
                <thead className="bg-[#0f1e35] text-slate-400 uppercase tracking-wide sticky top-0">
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
                      <td colSpan={4} className="px-3 py-6 text-center text-slate-500">Memuat...</td>
                    </tr>
                  ) : dbHistory.length === 0 ? (
                    <tr>
                      <td colSpan={4} className="px-3 py-8 text-center text-slate-500">
                        Belum ada history klasifikasi.
                      </td>
                    </tr>
                  ) : (
                    dbHistory.map((item, idx) => {
                      const cls = item.dominant_class.toLowerCase();

                      // Tentukan status berdasarkan dominant_class
                      const getStatus = (c: string): 'Normal' | 'Warning' | 'Critical' => {
                        if (c === 'normal') return 'Normal';
                        if (c.includes('cut') || c.includes('nearly')) return 'Critical';
                        return 'Warning';
                      };
                      const itemStatus = getStatus(cls);

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

                      const clsBadge =
                        cls === 'normal' ? 'bg-emerald-500/20 text-emerald-400' :
                        cls.includes('cut') || cls.includes('nearly') ? 'bg-red-500/20 text-red-400' :
                        cls.includes('bend') ? 'bg-amber-500/20 text-amber-400' :
                        cls.includes('air') || cls.includes('gap') ? 'bg-purple-500/20 text-purple-400' :
                        cls.includes('splice') ? 'bg-orange-500/20 text-orange-400' :
                        cls.includes('dirty') || cls.includes('connector') ? 'bg-cyan-500/20 text-cyan-400' :
                        'bg-blue-500/20 text-blue-400';

                      // Format tanggal: 25 Jul 2026 14:32:18
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
                          <td className="px-3 py-2 text-slate-400 font-mono">{idx + 1}</td>
                          <td className="px-3 py-2 text-slate-400 font-mono whitespace-nowrap">
                            {item.created_at ? formatDate(item.created_at) : '-'}
                          </td>
                          <td className="px-3 py-2">
                            <span className={`px-2 py-0.5 rounded-full text-[9px] font-semibold capitalize ${clsBadge}`}>
                              {item.dominant_class.replace(/_/g, ' ')}
                            </span>
                          </td>
                          <td className="px-3 py-2">
                            <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[9px] font-semibold ${statusBadge[itemStatus]}`}>
                              <span className={`w-1.5 h-1.5 rounded-full ${statusDot[itemStatus]}`} />
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

          {/* Fault Distribution — Donut Chart — 2/5 lebar */}
          <div className="lg:col-span-2 bg-[#1a2a45] border border-[#2a3d60] rounded-2xl overflow-hidden">
            <div className="flex items-center gap-2 px-4 py-3 border-b border-[#2a3d60]">
              <span className="w-1 h-4 bg-purple-400 rounded-full" />
              <span className="text-white text-sm font-semibold tracking-widest uppercase">Fault Distribution</span>
            </div>
            <div className="p-5">
              {!dbStats || Object.keys(dbStats.class_distribution).length === 0 ? (
                <div className="flex flex-col items-center justify-center h-56 text-slate-500 text-xs text-center">
                  <TrendingUp className="w-8 h-8 mb-2 opacity-30" />
                  <p>Belum ada data.</p>
                  <p className="mt-1">Upload file SOR untuk melihat distribusi.</p>
                </div>
              ) : (() => {
                const CLASS_LABELS: Record<string, string> = {
                  normal:          'NORMAL',
                  bending:         'BENDING',
                  bad_splice:      'BAD SPLICE',
                  dirty_connector: 'DIRTY CONNECTOR',
                  air_gap:         'AIR GAP',
                  nearly_cut:      'HAMPIR PUTUS',
                  fiber_cut:       'FIBER CUT',
                };
                const COLORS: Record<string, string> = {
                  normal:          '#10b981',
                  bending:         '#f59e0b',
                  bad_splice:      '#3b82f6',
                  dirty_connector: '#a855f7',
                  air_gap:         '#ef4444',
                  nearly_cut:      '#f97316',
                  fiber_cut:       '#ec4899',
                };

                // Normalisasi key dari API
                const rawDist = dbStats.class_distribution;
                const dist: Record<string, number> = {};
                Object.entries(rawDist).forEach(([k, v]) => {
                  const normalized = k.toLowerCase().replace(/\s+/g, '_');
                  dist[normalized] = (dist[normalized] || 0) + v;
                });

                const total = Object.values(dist).reduce((a, b) => a + b, 0);
                if (total === 0) return null;

                // Hanya kelas yang count > 0
                type Seg = { key: string; count: number; pct: number; color: string; label: string };
                const activeSegments: Seg[] = Object.entries(dist)
                  .filter(([, count]) => count > 0)
                  .sort(([, a], [, b]) => b - a)
                  .map(([k, count]) => ({
                    key: k,
                    count,
                    pct: count / total,
                    color: COLORS[k] || '#6366f1',
                    label: CLASS_LABELS[k] || k.replace(/_/g, ' ').toUpperCase(),
                  }));

                // SVG Donut — proporsional dengan panel
                const R = 85, r = 54, cx = 100, cy = 100;
                let cumPct = 0;
                const arcs = activeSegments.map(seg => {
                  const startPct = cumPct;
                  cumPct += seg.pct;
                  return { ...seg, startPct, endPct: cumPct };
                });

                const describeArc = (startPct: number, endPct: number) => {
                  const gap = activeSegments.length > 1 ? 0.005 : 0;
                  const s = (startPct + gap / 2) * 2 * Math.PI - Math.PI / 2;
                  const e = (endPct - gap / 2) * 2 * Math.PI - Math.PI / 2;
                  const x1 = cx + R * Math.cos(s), y1 = cy + R * Math.sin(s);
                  const x2 = cx + R * Math.cos(e), y2 = cy + R * Math.sin(e);
                  const x3 = cx + r * Math.cos(e), y3 = cy + r * Math.sin(e);
                  const x4 = cx + r * Math.cos(s), y4 = cy + r * Math.sin(s);
                  const large = (endPct - startPct) > 0.5 ? 1 : 0;
                  return `M ${x1} ${y1} A ${R} ${R} 0 ${large} 1 ${x2} ${y2} L ${x3} ${y3} A ${r} ${r} 0 ${large} 0 ${x4} ${y4} Z`;
                };

                // State tooltip — pakai object sederhana di dalam IIFE dengan React.useState tidak bisa,
                // jadi gunakan SVG <title> native + custom overlay via foreignObject trick.
                // Cara paling bersih: gunakan <title> di dalam <path> (native SVG tooltip).
                // Untuk tooltip custom yang lebih bagus, wrap SVG dalam div dengan state.
                // Kita pakai pendekatan: onMouseEnter/Leave pada path, tooltip div absolut.

                return (
                  <div className="flex flex-col items-center gap-5">
                    {/* Donut SVG dengan tooltip hover */}
                    <div className="relative">
                      <svg viewBox="0 0 200 200" className="w-44 h-44">
                        {activeSegments.length === 1 ? (
                          <>
                            <circle
                              cx={cx} cy={cy} r={R}
                              fill={activeSegments[0].color}
                              className="cursor-pointer"
                              style={{ filter: 'brightness(1)' }}
                              onMouseEnter={e => {
                                const el = e.currentTarget.closest('.relative')?.querySelector('.donut-tooltip') as HTMLElement;
                                if (el) {
                                  el.textContent = `${activeSegments[0].label}: ${activeSegments[0].count}`;
                                  el.style.display = 'block';
                                }
                                (e.currentTarget as SVGElement).style.filter = 'brightness(1.25)';
                              }}
                              onMouseLeave={e => {
                                const el = e.currentTarget.closest('.relative')?.querySelector('.donut-tooltip') as HTMLElement;
                                if (el) el.style.display = 'none';
                                (e.currentTarget as SVGElement).style.filter = 'brightness(1)';
                              }}
                            />
                            <circle cx={cx} cy={cy} r={r} fill="#1a2a45" style={{ pointerEvents: 'none' }} />
                          </>
                        ) : (
                          arcs.map(seg => (
                            <path
                              key={seg.key}
                              d={describeArc(seg.startPct, seg.endPct)}
                              fill={seg.color}
                              className="cursor-pointer transition-all duration-150"
                              style={{ filter: 'brightness(1)' }}
                              onMouseEnter={e => {
                                const el = e.currentTarget.closest('.relative')?.querySelector('.donut-tooltip') as HTMLElement;
                                if (el) {
                                  el.textContent = `${seg.label}: ${seg.count}`;
                                  el.style.display = 'block';
                                }
                                (e.currentTarget as SVGElement).style.filter = 'brightness(1.3)';
                                (e.currentTarget as SVGElement).style.transform = 'scale(1.03)';
                                (e.currentTarget as SVGElement).style.transformOrigin = '100px 100px';
                              }}
                              onMouseLeave={e => {
                                const el = e.currentTarget.closest('.relative')?.querySelector('.donut-tooltip') as HTMLElement;
                                if (el) el.style.display = 'none';
                                (e.currentTarget as SVGElement).style.filter = 'brightness(1)';
                                (e.currentTarget as SVGElement).style.transform = 'scale(1)';
                              }}
                            />
                          ))
                        )}
                      </svg>
                      {/* Tooltip overlay */}
                      <div
                        className="donut-tooltip absolute left-1/2 -translate-x-1/2 -top-8 bg-[#0f1e35] border border-[#2a3d60] text-white text-[10px] font-semibold px-2.5 py-1 rounded-lg whitespace-nowrap pointer-events-none shadow-lg"
                        style={{ display: 'none' }}
                      />
                    </div>

                    {/* Legend — hanya kelas aktif, 1 kolom */}
                    <div className="w-full space-y-2 px-1">
                      {activeSegments.map(seg => (
                        <div key={seg.key} className="flex items-center gap-2">
                          <span
                            className="w-2.5 h-2.5 rounded-full flex-shrink-0"
                            style={{ backgroundColor: seg.color }}
                          />
                          <span className="text-[10px] font-semibold tracking-wide text-slate-300">
                            {seg.label}
                          </span>
                          <span className="text-[11px] font-bold font-mono text-white ml-1">
                            {seg.count}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })()}
            </div>
          </div>

        </div>{/* end grid Classification History + Fault Distribution */}

      </main>
    </div>
  );
};

export default MainDashboard;