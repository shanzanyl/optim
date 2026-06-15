// frontend/src/components/Detection.tsx
import React, { useState, useRef, useEffect } from 'react';
import { Camera, RefreshCw, AlertTriangle, Info, Edit3 } from 'lucide-react';
import { useSlide } from '../Context/SlideContext';
import { triggerSlideAlert } from '../services/api';

interface DetectionProps {
  refreshTrigger?: number;
  onDataChange?: () => void;
}

const API_BASE = import.meta.env.VITE_API_URL || 'https://optim-api-ckfhb5heg3f3btgz.southeastasia-01.azurewebsites.net';

interface HistoryRow {
  id: number;
  loss_1: number | null;
  loss_2: number | null;
  loss_3: number | null;
  loss_4: number | null;
  total_l_4: number | null;
  return_1: number | null;
  return_2: number | null;
  return_3: number | null;
  return_4: number | null;
  prx: number | null;
  klasifikasi: string | null;
  status: string | null;
  timestamp: string | null;
}

interface LastResult {
  prediction: string;
  confidence: number;
  status: string;
  prx: number;
  prx_source: 'manual' | 'ocr' | 'default';
  extracted: {
    distances: number[];
    losses: number[];
    returns: number[];
    total_ls: number[];
    avg_ls: number[];
    avg_total: number;
  };
  raw_text: string;
}

const StatusBadge = ({ status }: { status: string | null }) => {
  const cfg: Record<string, string> = {
    'Normal': 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
    'Warning': 'bg-amber-500/15 text-amber-400 border-amber-500/30',
    'Critical': 'bg-red-500/15 text-red-400 border-red-500/30',
  };
  const dot: Record<string, string> = {
    'Normal': 'bg-emerald-400',
    'Warning': 'bg-amber-400',
    'Critical': 'bg-red-400',
  };
  const s = status || 'Warning';
  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-black uppercase border ${cfg[s] || cfg.Warning}`}>
      <span className={`w-1.5 h-1.5 rounded-full animate-pulse ${dot[s] || dot.Warning}`} />
      {s}
    </span>
  );
};

const formatLossValue = (value: number | null | undefined) => {
  if (value === null || value === undefined || value === 0) return '---';
  return value.toFixed(2);
};

const formatReturnValue = (value: number | null | undefined) => {
  if (value === null || value === undefined) return '---';
  return value.toFixed(1);
};

const Detection = ({ refreshTrigger, onDataChange }: DetectionProps) => {
  const [allHistory, setAllHistory] = useState<HistoryRow[]>([]);
  const [lastResult, setLastResult] = useState<LastResult | null>(null);
  const [imageStatus, setImageStatus] = useState<'idle' | 'uploading' | 'success' | 'error'>('idle');
  const [errorMsg, setErrorMsg] = useState('');
  const [preview, setPreview] = useState<string | null>(null);
  const [isLoadingHistory, setIsLoadingHistory] = useState(true);
  const [prxManual, setPrxManual] = useState<string>('');
  const [showRawOcr, setShowRawOcr] = useState(false);
  const imageInputRef = useRef<HTMLInputElement>(null);

  const [activeInputMethod, setActiveInputMethod] = useState<'ocr' | 'manual'>('ocr');
  const [manualForm, setManualForm] = useState({
    prx: '',
    avg_total: '',
    distance_1: '', distance_2: '', distance_3: '', distance_4: '',
    loss_1: '', loss_2: '', loss_3: '', loss_4: '',
    total_l_1: '', total_l_2: '', total_l_3: '', total_l_4: '',
    avg_l_1: '', avg_l_2: '', avg_l_3: '', avg_l_4: '',
    return_1: '', return_2: '', return_3: '', return_4: ''
  });

  const handleManualClassify = async (e: React.FormEvent) => {
    e.preventDefault();
    setImageStatus('uploading');
    setErrorMsg('');
    setLastResult(null);

    const payload = {
      prx: parseFloat(manualForm.prx) || 0.0,
      avg_total: parseFloat(manualForm.avg_total) || 0.0,
      distance_1: parseFloat(manualForm.distance_1) || 0.0,
      distance_2: parseFloat(manualForm.distance_2) || 0.0,
      distance_3: parseFloat(manualForm.distance_3) || 0.0,
      distance_4: parseFloat(manualForm.distance_4) || 0.0,
      loss_1: parseFloat(manualForm.loss_1) || 0.0,
      loss_2: parseFloat(manualForm.loss_2) || 0.0,
      loss_3: parseFloat(manualForm.loss_3) || 0.0,
      loss_4: parseFloat(manualForm.loss_4) || 0.0,
      total_l_1: parseFloat(manualForm.total_l_1) || 0.0,
      total_l_2: parseFloat(manualForm.total_l_2) || 0.0,
      total_l_3: parseFloat(manualForm.total_l_3) || 0.0,
      total_l_4: parseFloat(manualForm.total_l_4) || 0.0,
      avg_l_1: parseFloat(manualForm.avg_l_1) || 0.0,
      avg_l_2: parseFloat(manualForm.avg_l_2) || 0.0,
      avg_l_3: parseFloat(manualForm.avg_l_3) || 0.0,
      avg_l_4: parseFloat(manualForm.avg_l_4) || 0.0,
      return_1: parseFloat(manualForm.return_1) || 0.0,
      return_2: parseFloat(manualForm.return_2) || 0.0,
      return_3: parseFloat(manualForm.return_3) || 0.0,
      return_4: parseFloat(manualForm.return_4) || 0.0,
    };

    const token = localStorage.getItem('token');
    try {
      const response = await fetch(`${API_BASE}/api/classify-manual`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { 'Authorization': `Bearer ${token}` } : {})
        },
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || 'Failed to classify data');
      }

      const result = await response.json();
      setLastResult(result);
      setImageStatus('success');
      await fetchHistory();
      if (onDataChange) onDataChange();
    } catch (error: any) {
      console.error('Manual classification error:', error);
      setErrorMsg(error.message || 'Failed to connect to server.');
      setImageStatus('error');
    }
  };

  const {
    currentIndex,
    setCurrentIndex,
    totalData,
    setTotalData,
    autoPlay,
    setAutoPlay
  } = useSlide();

  const [prevTotalData, setPrevTotalData] = useState(0);

  // useEffect(() => {
  //   if (totalData !== prevTotalData) {
  //     setPrevTotalData(totalData);
  //   }
  // }, [totalData, prevTotalData]);

  // useEffect(() => {
  //   if (allHistory.length > 0 && currentIndex >= allHistory.length) {
  //     setCurrentIndex(0);
  //   }
  // }, [allHistory.length, currentIndex, setCurrentIndex]);

  // useEffect(() => {
  //   setTotalData(allHistory.length);
  // }, [allHistory.length, setTotalData]);

  // 🔥 PERBAIKAN 1: Effect untuk trigger Telegram Alert (dipisah dari autoPlay)
  useEffect(() => {
    if (isLoadingHistory || allHistory.length === 0 || currentIndex < 0 || currentIndex >= allHistory.length) return;
    const currentRecord = allHistory[currentIndex];
    if (!currentRecord) return;

    const status = currentRecord.status || '';
    if (status.toLowerCase() === 'warning' || status.toLowerCase() === 'critical') {
      triggerSlideAlert(currentRecord.id)
        .then((res: { status: string }) => {
          if (res.status === 'sent') {
            console.log(`Telegram alert sent automatically from Detection for record ID: ${currentRecord.id}`);
          }
        })
        .catch((err: any) => console.error('Error triggering slide alert:', err));
    }
  }, [currentIndex, allHistory, isLoadingHistory]);

  // 🔥 PERBAIKAN 2: Effect untuk AutoPlay (yang sudah ada, tanpa nested useEffect)
  useEffect(() => {
    if (!autoPlay || allHistory.length === 0) return;

    const interval = setInterval(() => {
      setCurrentIndex((prev: number) => {
        const newTotal = allHistory.length;
        if (newTotal > prevTotalData) {
          setPrevTotalData(newTotal);
          return newTotal - 1;
        }
        if (prev === newTotal - 1) {
          return 0;
        }
        return prev + 1;
      });
    }, 30000);

    return () => clearInterval(interval);
  }, [autoPlay, allHistory.length, prevTotalData, setCurrentIndex]);

  // 🔥 PERUBAHAN: Hanya menampilkan data dari source 'ocr' dan 'manual' (tidak termasuk 'sheets')
  const fetchHistory = async () => {
    setIsLoadingHistory(true);
    try {
      const token = localStorage.getItem('token');
      const response = await fetch(`${API_BASE}/api/history?limit=5000`, {
        headers: { 'Authorization': `Bearer ${token}` },
      });
      if (!response.ok) throw new Error('Failed to fetch history');
      const result = await response.json();

      // 🔥 FILTER: Hanya ambil data dengan source 'ocr' atau 'manual'
      const mappedHistory = (result.history || [])
        .filter((record: any) => record.source === 'ocr' || record.source === 'manual')
        .map((record: any) => ({
          id: record.id,
          loss_1: record.loss_1 ?? null,
          loss_2: record.loss_2 ?? null,
          loss_3: record.loss_3 ?? null,
          loss_4: record.loss_4 ?? null,
          total_l_4: record.total_l_4 ?? null,
          return_1: record.return_1 ?? null,
          return_2: record.return_2 ?? null,
          return_3: record.return_3 ?? null,
          return_4: record.return_4 ?? null,
          prx: record.prx ?? null,
          klasifikasi: record.klasifikasi,
          status: record.status,
          timestamp: record.timestamp,
        }));

      const sorted = [...mappedHistory].sort(
        (a, b) => new Date(a.timestamp || 0).getTime() - new Date(b.timestamp || 0).getTime()
      );
      setAllHistory(sorted);
    } catch (err) {
      console.error('History fetch error:', err);
    } finally {
      setIsLoadingHistory(false);
    }
  };

  useEffect(() => {
    fetchHistory();
  }, []);

  useEffect(() => {
    if (refreshTrigger && refreshTrigger > 0) {
      fetchHistory();
    }
  }, [refreshTrigger]);

  const handleImageUpload = async (file: File) => {
    setImageStatus('uploading');
    setErrorMsg('');
    setLastResult(null);
    setShowRawOcr(false);

    const reader = new FileReader();
    reader.onload = (e) => setPreview(e.target?.result as string);
    reader.readAsDataURL(file);

    const formData = new FormData();
    formData.append('file', file);
    if (prxManual.trim() !== '') {
      const prxVal = parseFloat(prxManual);
      if (!isNaN(prxVal)) {
        formData.append('prx_manual', String(prxVal));
      }
    }

    const token = localStorage.getItem('token');
    try {
      const response = await fetch(`${API_BASE}/api/detect`, {
        method: 'POST',
        headers: token ? { 'Authorization': `Bearer ${token}` } : {},
        body: formData,
      });

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || 'Failed to process image');
      }

      const result = await response.json();

      if (result.extracted) {
        if (!result.extracted.avg_ls && result.extracted.total_ls && result.extracted.distances) {
          result.extracted.avg_ls = result.extracted.total_ls.map((total: number, i: number) => {
            const dist = result.extracted.distances[i] || 1;
            return total / dist;
          });
        }
        if (!result.extracted.avg_total && result.extracted.total_ls && result.extracted.distances) {
          const totalTotalL = result.extracted.total_ls[3] || 0;
          const totalDistance = result.extracted.distances[3] || 1;
          result.extracted.avg_total = totalTotalL / totalDistance;
        }
      }

      setLastResult(result);
      setImageStatus('success');
      await fetchHistory();
      if (onDataChange) onDataChange();
    } catch (error: any) {
      console.error('OCR error:', error);
      setErrorMsg(error.message || 'Failed to connect. Ensure backend is running on port 8000');
      setImageStatus('error');
    }
  };

  const displayedHistory = [...allHistory].reverse();
  const progressPercent = totalData > 0 ? ((currentIndex + 1) / totalData) * 100 : 0;

  function handleSync(event: React.MouseEvent<HTMLButtonElement>): void {
    throw new Error('Function not implemented.');
  }

  return (
    <div className="min-h-screen bg-[#14213d] text-slate-300 font-sans pb-20 w-full">
      <div className="space-y-6 p-6">
        {/* Progress Slideshow - SAMA SEPERTI DASHBOARD */}
        <div className="bg-[#1e2f50] border border-[#3b4f6e] rounded-2xl p-4">
          <div className="flex justify-between items-center mb-2">
            <span className="text-xs text-white">Slide Show Progress (Classification)</span>
            <div className="flex items-center gap-3">
              <span className="text-xs text-white font-mono">{currentIndex + 1} / {totalData}</span>
            </div>
          </div>
          <div className="w-full bg-slate-600 rounded-full h-2">
            <div
              className="bg-emerald-500 h-2 rounded-full transition-all duration-500"
              style={{ width: `${progressPercent}%` }}
            />
          </div>
        </div>

        {/* Upload + Hasil */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {/* Input & Form Card */}
          <div className={`bg-[#1e2f50] p-6 sm:p-8 rounded-2xl border transition-all ${imageStatus === 'success' ? 'border-emerald-500/50 bg-emerald-500/5' :
            imageStatus === 'error' ? 'border-red-500/50' :
              imageStatus === 'uploading' ? 'border-blue-500/50 bg-blue-500/5' :
                'border-[#3b4f6e] hover:border-blue-500/50'
            }`}>
            {/* Toggle Metode Input */}
            <div className="flex bg-[#0f1a2e] p-1 rounded-xl mb-6 border border-[#3b4f6e]">
              <button
                type="button"
                onClick={() => {
                  setActiveInputMethod('ocr');
                  setImageStatus('idle');
                  setErrorMsg('');
                }}
                className={`flex-1 py-2 text-sm font-semibold rounded-lg transition-all ${activeInputMethod === 'ocr'
                  ? 'bg-blue-600 text-white shadow-lg'
                  : 'text-slate-400 hover:text-white'
                  }`}
              >
                OCR (Upload Foto)
              </button>
              <button
                type="button"
                onClick={() => {
                  setActiveInputMethod('manual');
                  setImageStatus('idle');
                  setErrorMsg('');
                }}
                className={`flex-1 py-2 text-sm font-semibold rounded-lg transition-all ${activeInputMethod === 'manual'
                  ? 'bg-blue-600 text-white shadow-lg'
                  : 'text-slate-400 hover:text-white'
                  }`}
              >
                Input Manual
              </button>
            </div>

            {activeInputMethod === 'ocr' ? (
              <>
                <Camera className="w-12 h-12 text-blue-500 mx-auto mb-4" />
                <h3 className="text-lg font-semibold mb-2 text-white text-center">Upload Photo OTDR</h3>
                <p className="text-xs text-slate-500 mb-5 text-center">Format: JPG, PNG</p>

                {preview && (
                  <div className="mb-4 rounded-xl overflow-hidden border border-[#3b4f6e]">
                    <img src={preview} alt="Preview OTDR" className="w-full h-40 object-cover" />
                  </div>
                )}

                <div className="mb-5">
                  <label className="text-sm font-bold text-white mb-1.5 block">
                    Input Prx Value (dBm)
                  </label>
                  <div className="flex items-center gap-2">
                    <input
                      type="number"
                      step="0.1"
                      value={prxManual}
                      onChange={e => setPrxManual(e.target.value)}
                      placeholder="-15.6"
                      className="flex-1 px-3 py-2 bg-[#0f1a2e] border border-[#3b4f6e] rounded-lg
                        text-white text-sm focus:ring-2 focus:ring-blue-500/50 outline-none placeholder:text-slate-500"
                    />
                    <span className="text-sm text-white whitespace-nowrap">dBm</span>
                  </div>
                </div>

                <input
                  type="file"
                  accept="image/*"
                  className="hidden"
                  id="image-upload"
                  ref={imageInputRef}
                  onChange={e => e.target.files?.[0] && handleImageUpload(e.target.files[0])}
                />
                <label
                  htmlFor="image-upload"
                  className={`w-full px-6 py-2.5 rounded-xl cursor-pointer flex items-center justify-center
                    transition-all font-semibold text-white text-sm ${imageStatus === 'uploading'
                      ? 'bg-slate-600 cursor-wait'
                      : 'bg-blue-600 hover:bg-blue-500 shadow-lg shadow-blue-600/20'
                    }`}
                >
                  {imageStatus === 'uploading' ? 'Processing...' :
                    imageStatus === 'success' ? 'Upload Again' :
                      imageStatus === 'error' ? 'Try Again' : 'Select OTDR Photo'}
                </label>
              </>
            ) : (
              <form onSubmit={handleManualClassify} className="space-y-4">
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="text-[12px] font-bold text-white uppercase tracking-widest mb-1.5 block">
                      Prx (dBm)
                    </label>
                    <input
                      type="number"
                      step="0.01"
                      required
                      value={manualForm.prx}
                      onChange={e => setManualForm({ ...manualForm, prx: e.target.value })}
                      placeholder="-15.60"
                      className="w-full px-3 py-2 bg-[#0f1a2e] border border-[#3b4f6e] rounded-lg
                        text-white text-xs focus:ring-2 focus:ring-blue-500/50 outline-none placeholder:text-slate-600 font-mono"
                    />
                  </div>
                  <div>
                    <label className="text-[12px] font-bold text-white uppercase tracking-widest mb-1.5 block">
                      Avg-Total (dB/km)
                    </label>
                    <input
                      type="number"
                      step="0.001"
                      required
                      value={manualForm.avg_total}
                      onChange={e => setManualForm({ ...manualForm, avg_total: e.target.value })}
                      placeholder="0.250"
                      className="w-full px-3 py-2 bg-[#0f1a2e] border border-[#3b4f6e] rounded-lg
                        text-white text-xs focus:ring-2 focus:ring-blue-500/50 outline-none placeholder:text-slate-600 font-mono"
                    />
                  </div>
                </div>

                <div className="border border-[#3b4f6e]/50 rounded-xl overflow-x-auto mt-4">
                  <table className="w-full text-left text-xs min-w-[500px]">
                    <thead>
                      <tr className="bg-[#0f1a2e] text-white font-bold border-b border-[#3b4f6e]/50">
                        <th className="p-2">Section</th>
                        <th className="p-2">Distance (km)</th>
                        <th className="p-2">Loss (dB)</th>
                        <th className="p-2">Total-L (dB)</th>
                        <th className="p-2">Avg-L (dB/km)</th>
                        <th className="p-2">Return (dB)</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[#3b4f6e]/30">
                      {[1, 2, 3, 4].map(km => (
                        <tr key={km} className="hover:bg-[#0f1a2e]/20">
                          <td className="p-2 font-bold text-white text-xs">KM {km}</td>
                          <td className="p-2">
                            <input
                              type="number"
                              step="0.00001"
                              required
                              value={manualForm[`distance_${km}` as keyof typeof manualForm]}
                              onChange={e => setManualForm({ ...manualForm, [`distance_${km}`]: e.target.value })}
                              placeholder="0.0"
                              className="w-full px-1.5 py-1 bg-[#0f1a2e]/50 border border-[#3b4f6e] rounded text-white text-[11px] font-mono outline-none"
                            />
                          </td>
                          <td className="p-2">
                            <input
                              type="number"
                              step="0.001"
                              required={km !== 4}
                              disabled={km === 4}
                              value={km === 4 ? '' : manualForm[`loss_${km}` as keyof typeof manualForm]}
                              onChange={e => setManualForm({ ...manualForm, [`loss_${km}`]: e.target.value })}
                              placeholder={km === 4 ? '—' : '0.0'}
                              className="w-full px-1.5 py-1 bg-[#0f1a2e]/50 border border-[#3b4f6e] rounded text-white text-[11px] font-mono outline-none disabled:opacity-45"
                            />
                          </td>
                          <td className="p-2">
                            <input
                              type="number"
                              step="0.001"
                              required
                              value={manualForm[`total_l_${km}` as keyof typeof manualForm]}
                              onChange={e => setManualForm({ ...manualForm, [`total_l_${km}`]: e.target.value })}
                              placeholder="0.0"
                              className="w-full px-1.5 py-1 bg-[#0f1a2e]/50 border border-[#3b4f6e] rounded text-white text-[11px] font-mono outline-none"
                            />
                          </td>
                          <td className="p-2">
                            <input
                              type="number"
                              step="0.001"
                              required
                              value={manualForm[`avg_l_${km}` as keyof typeof manualForm]}
                              onChange={e => setManualForm({ ...manualForm, [`avg_l_${km}`]: e.target.value })}
                              placeholder="0.0"
                              className="w-full px-1.5 py-1 bg-[#0f1a2e]/50 border border-[#3b4f6e] rounded text-white text-[11px] font-mono outline-none"
                            />
                          </td>
                          <td className="p-2">
                            <input
                              type="number"
                              step="0.01"
                              required
                              value={manualForm[`return_${km}` as keyof typeof manualForm]}
                              onChange={e => setManualForm({ ...manualForm, [`return_${km}`]: e.target.value })}
                              placeholder="0.0"
                              className="w-full px-1.5 py-1 bg-[#0f1a2e]/50 border border-[#3b4f6e] rounded text-white text-[11px] font-mono outline-none"
                            />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                <button
                  type="submit"
                  disabled={imageStatus === 'uploading'}
                  className={`w-full px-6 py-2.5 rounded-xl flex items-center justify-center
                    transition-all font-semibold text-white text-sm mt-4 ${imageStatus === 'uploading'
                      ? 'bg-slate-600 cursor-wait'
                      : 'bg-blue-600 hover:bg-blue-500 shadow-lg shadow-blue-600/20'
                    }`}
                >
                  {imageStatus === 'uploading' ? 'Memproses Klasifikasi...' : 'Proses Klasifikasi Manual'}
                </button>
              </form>
            )}
          </div>

          {/* Hasil Terakhir */}
          <div className="bg-[#1e2f50] p-8 rounded-2xl border border-[#3b4f6e]">
            <h3 className="text-lg font-bold text-white mb-4 uppercase tracking-widest">Result Classification</h3>
            {imageStatus === 'uploading' && (
              <div className="flex flex-col items-center justify-center h-52 gap-3">
                <RefreshCw className="w-8 h-8 text-blue-400 animate-spin" />
                <p className="text-sm text-slate-400">OCR is reading the image...</p>
              </div>
            )}
            {imageStatus === 'idle' && !lastResult && (
              <div className="flex flex-col items-center justify-center h-52 gap-2 text-slate-500">
                <Camera className="w-8 h-8" />
                <p className="text-sm italic">Upload photo OTDR to view results</p>
              </div>
            )}
            {errorMsg && (
              <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-4 flex gap-3">
                <AlertTriangle className="w-5 h-5 text-red-400 flex-shrink-0 mt-0.5" />
                <p className="text-sm text-red-400">{errorMsg}</p>
              </div>
            )}
            {lastResult && imageStatus === 'success' && (
              <div className="space-y-4">
                <div className={`rounded-xl p-4 text-center border ${lastResult.status === 'Normal' ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30' :
                  lastResult.status === 'Critical' ? 'bg-red-500/10 text-red-400 border-red-500/30' :
                    'bg-amber-500/10 text-amber-400 border-amber-500/30'
                  }`}>
                  <p className="text-xs uppercase tracking-widest mb-1 opacity-70">Klasifikasi</p>
                  <p className="text-2xl font-black">{lastResult.prediction}</p>
                </div>

                <div className="bg-blue-500/10 border border-blue-500/20 rounded-xl p-3 flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Info size={14} className="text-blue-400" />
                    <span className="text-xs text-blue-400 font-bold">Signal Power (Prx)</span>
                  </div>
                  <div className="text-right">
                    <span className="text-white font-black text-sm">{lastResult.prx?.toFixed(1)} dBm</span>
                    <span className="text-[10px] text-white ml-2">
                      ({lastResult.prx_source === 'manual' ? 'input manual' :
                        lastResult.prx_source === 'ocr' ? 'dari OCR' : 'default'})
                    </span>
                  </div>
                </div>

                {/* Nilai Terdeteksi */}
                <div className="space-y-2">
                  <p className="text-[10px] font-bold text-slate-500 uppercase tracking-widest">Detected Values</p>

                  <div className="grid grid-cols-2 gap-2 text-xs">
                    {lastResult.extracted.losses?.map((l: number, i: number) => (
                      <div key={`loss-${i}`} className="bg-[#0f1a2e] rounded-lg p-2 flex justify-between">
                        <span className="text-white">Loss KM {i + 1}</span>
                        <span className="text-white font-mono">
                          {l === 0 || l === null || l === undefined ? '---' : l.toString()} dB
                        </span>
                      </div>
                    ))}
                  </div>

                  {lastResult.extracted.total_ls && (
                    <div className="grid grid-cols-2 gap-2 text-xs mt-2">
                      {lastResult.extracted.total_ls.map((tl: number, i: number) => (
                        <div key={`total-${i}`} className="bg-[#0f1a2e] rounded-lg p-2 flex justify-between">
                          <span className="text-white">Total-L KM {i + 1}</span>
                          <span className="text-white font-mono">
                            {tl === 0 || tl === null || tl === undefined ? '---' : tl.toString()} dB
                          </span>
                        </div>
                      ))}
                    </div>
                  )}

                  {lastResult.extracted.avg_ls && (
                    <div className="grid grid-cols-2 gap-2 text-xs mt-2">
                      {lastResult.extracted.avg_ls.map((al: number, i: number) => (
                        <div key={`avg-${i}`} className="bg-[#0f1a2e] rounded-lg p-2 flex justify-between">
                          <span className="text-white">Avg-L KM {i + 1}</span>
                          <span className="text-white font-mono">
                            {al === 0 ? '---' : al.toString()} dB/km
                          </span>
                        </div>
                      ))}
                    </div>
                  )}

                  {lastResult.extracted.avg_total !== undefined && (
                    <div className="bg-[#0f1a2e] rounded-lg p-2 flex justify-between text-xs mt-2">
                      <span className="text-white">Avg-Total</span>
                      <span className="text-white font-mono">
                        {lastResult.extracted.avg_total === 0 ? '---' : lastResult.extracted.avg_total.toString()} dB/km
                      </span>
                    </div>
                  )}

                  <div className="grid grid-cols-2 gap-2 text-xs mt-2">
                    {lastResult.extracted.returns?.map((r: number, i: number) => (
                      <div key={`ret-${i}`} className="bg-[#0f1a2e] rounded-lg p-2 flex justify-between">
                        <span className="text-white">Return KM {i + 1}</span>
                        <span className="text-white font-mono">{r.toString()} dB</span>
                      </div>
                    ))}
                  </div>
                </div>

                <button
                  onClick={() => setShowRawOcr(!showRawOcr)}
                  className="text-[10px] text-slate-500 hover:text-slate-400 underline transition"
                >
                  {showRawOcr ? '▲ Hide' : '▼ Show'} raw OCR text
                </button>
                {showRawOcr && (
                  <pre className="text-[9px] bg-[#0f1a2e] border border-[#3b4f6e] rounded-lg p-3 
                    text-slate-400 overflow-x-auto max-h-32 whitespace-pre-wrap">
                    {lastResult.raw_text}
                  </pre>
                )}
              </div>
            )}
          </div>
        </div>

        {/* History Table dengan Total-L dari total_l_4 */}
        <div className="bg-[#1e2f50] border border-[#3b4f6e] rounded-[2.5rem] shadow-2xl overflow-hidden">
          <div className="p-5 border-b border-[#3b4f6e] flex justify-between items-center">
            <h2 className="text-sm font-bold text-white uppercase tracking-widest">History of Measurements</h2>
            <div className="flex items-center gap-3">
              <span className="text-xs text-slate-400 bg-[#0f1a2e] px-3 py-1 rounded-full border border-[#3b4f6e]">
                {displayedHistory.length} data
              </span>
              <button onClick={fetchHistory} className="text-slate-500 hover:text-white transition-colors" title="Refresh">
                <RefreshCw size={14} className={isLoadingHistory ? 'animate-spin' : ''} />
              </button>
            </div>
          </div>

          <div className="overflow-x-auto max-h-[500px] overflow-y-auto">
            <table className="w-full text-left">
              <thead className="sticky top-0 bg-[#1e2f50] z-10">
                <tr className="bg-[#1e2f50] text-white text-[13px] font-black uppercase tracking-widest border-b border-[#3b4f6e]">
                  <th className="px-6 py-4">Time</th>
                  <th className="px-6 py-4 text-center">Loss KM1-4 (dB)</th>
                  <th className="px-6 py-4 text-center">Total-L (dB)</th>
                  <th className="px-6 py-4 text-center">Return KM1-4 (dB)</th>
                  <th className="px-6 py-4 text-center">PRX (dBm)</th>
                  <th className="px-6 py-4">Classification</th>
                  <th className="px-6 py-4 text-center">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#3b4f6e]/50">
                {isLoadingHistory ? (
                  <tr><td colSpan={7} className="px-6 py-12 text-center text-slate-500"><RefreshCw size={18} className="animate-spin mx-auto" /></td></tr>
                ) : displayedHistory.length === 0 ? (
                  <tr><td colSpan={7} className="px-6 py-12 text-center text-slate-500 italic">No measurement history available.</td></tr>
                ) : (
                  displayedHistory.map((row, idx) => {
                    const recordTime = row.timestamp ? new Date(row.timestamp).toLocaleString('en-US') : '—';
                    const totalLValue = row.total_l_4;
                    const totalLDisplay = !totalLValue || totalLValue === 0 ? '---' : totalLValue.toFixed(2);
                    
                    return (
                      <tr key={row.id || idx} className="hover:bg-[#2a3d60]/20 transition-colors">
                        <td className="px-6 py-4 text-slate-400 text-xs font-mono">{recordTime}</td>
                        <td className="px-6 py-4 text-center text-white text-xs font-mono">
                          {formatLossValue(row.loss_1)} | {formatLossValue(row.loss_2)} |{' '}
                          {formatLossValue(row.loss_3)} | {formatLossValue(row.loss_4)}
                        </td>
                        <td className="px-6 py-4 text-center text-emerald-400 font-bold text-xs font-mono">
                          {totalLDisplay}
                        </td>
                        <td className="px-6 py-4 text-center text-white text-xs font-mono">
                          {formatReturnValue(row.return_1)} | {formatReturnValue(row.return_2)} |{' '}
                          {formatReturnValue(row.return_3)} | {formatReturnValue(row.return_4)}
                        </td>
                        <td className="px-6 py-4 text-center text-blue-400 font-bold text-xs font-mono">
                          {row.prx != null ? `${row.prx.toFixed(1)} dBm` : '—'}
                        </td>
                        <td className="px-6 py-4">
                          <span className={`px-3 py-1 rounded-full text-[11px] font-black border ${
                            row.klasifikasi === 'Normal' ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30' :
                            row.klasifikasi === 'Warning' ? 'bg-amber-500/20 text-amber-400 border-amber-500/30' :
                            row.klasifikasi === 'Fiber Cut' ? 'bg-red-500/20 text-red-400 border-red-500/30' :
                            'bg-amber-500/20 text-amber-400 border-amber-500/30'
                          }`}>
                            {row.klasifikasi === 'hampir putus' ? 'Nearly Cut' : (row.klasifikasi || 'Unknown')}
                          </span>
                        </td>
                        <td className="px-6 py-4 text-center"><StatusBadge status={row.status} /></td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
};

export default Detection;