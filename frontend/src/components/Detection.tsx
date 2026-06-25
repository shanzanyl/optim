// frontend/src/components/Detection.tsx
import React, { useState, useRef, useEffect } from 'react';
import { Camera, RefreshCw, AlertTriangle, Info, Edit3, CheckCircle, Send } from 'lucide-react';
import { useSlide } from '../Context/SlideContext';
// import { triggerSlideAlert } from '../services/api';

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
    losses: (number | null)[];
    returns: number[];
    total_ls: number[];
    avg_ls: number[];
    avg_total: number;
  };
  raw_text: string;
}

interface OcrParseResult {
  message: string;
  raw_text: string;
  ocr_method: string;
  extracted: {
    distances: number[];
    losses: (number | null)[];
    total_ls: number[];
    avg_ls: number[];
    returns: number[];
    avg_total: number;
  };
  prx: number;
  per_km: any;
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

  const [ocrParseResult, setOcrParseResult] = useState<OcrParseResult | null>(null);
  const [isOcrParsed, setIsOcrParsed] = useState(false);

  const [displayTimestamps, setDisplayTimestamps] = useState<Record<number, string>>(() => {
    try {
      const saved = localStorage.getItem('detection_display_timestamps');
      return saved ? JSON.parse(saved) : {};
    } catch {
      return {};
    }
  });

  // const [sentAlerts, setSentAlerts] = useState<Set<number>>(new Set());

  useEffect(() => {
    localStorage.setItem('detection_display_timestamps', JSON.stringify(displayTimestamps));
  }, [displayTimestamps]);

  useEffect(() => {
    // Hanya jalan saat allHistory update dan ada lastResult —
    // saat ini record baru sudah pasti ada di allHistory (fetchHistory sudah await selesai)
    if (!lastResult || allHistory.length === 0) return;
    const latestRecord = allHistory[allHistory.length - 1];
    if (!latestRecord) return;
    setDisplayTimestamps(prev => {
      // Jika id ini sudah ada timestampnya, jangan timpa
      if (prev[latestRecord.id]) return prev;
      return {
        ...prev,
        [latestRecord.id]: new Date().toISOString()
      };
    });
  }, [allHistory]); // ← hanya allHistory, bukan lastResult

  const formatDisplayTime = (timestamp: string | null) => {
    if (!timestamp) return '—';
    const date = new Date(timestamp);
    if (isNaN(date.getTime())) return '—';
    
    const day = String(date.getDate()).padStart(2, '0');
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const year = date.getFullYear();
    const hours = String(date.getHours()).padStart(2, '0');
    const minutes = String(date.getMinutes()).padStart(2, '0');
    
    return `${day}/${month}/${year} ${hours}.${minutes}`;
  };

  // 🔥 POPULATE FORM DARI OCR
  const populateFormFromOcr = (ocrData: OcrParseResult) => {
    const { extracted, prx } = ocrData;
    
    setManualForm({
      prx: prx !== undefined && prx !== null ? prx.toString() : '',
      avg_total: extracted.avg_total !== undefined && extracted.avg_total !== 0 ? extracted.avg_total.toString() : '',
      distance_1: extracted.distances[0]?.toString() || '',
      distance_2: extracted.distances[1]?.toString() || '',
      distance_3: extracted.distances[2]?.toString() || '',
      distance_4: extracted.distances[3]?.toString() || '',
      loss_1: extracted.losses[0]?.toString() || '',
      loss_2: extracted.losses[1]?.toString() || '',
      loss_3: extracted.losses[2]?.toString() || '',
      loss_4: extracted.losses[3] !== null && extracted.losses[3] !== undefined && extracted.losses[3] !== 0 
        ? extracted.losses[3].toString() 
        : '',
      total_l_1: extracted.total_ls[0]?.toString() || '',
      total_l_2: extracted.total_ls[1]?.toString() || '',
      total_l_3: extracted.total_ls[2]?.toString() || '',
      total_l_4: extracted.total_ls[3]?.toString() || '',
      avg_l_1: extracted.avg_ls[0]?.toString() || '',
      avg_l_2: extracted.avg_ls[1]?.toString() || '',
      avg_l_3: extracted.avg_ls[2]?.toString() || '',
      avg_l_4: extracted.avg_ls[3]?.toString() || '',
      return_1: extracted.returns[0]?.toString() || '',
      return_2: extracted.returns[1]?.toString() || '',
      return_3: extracted.returns[2]?.toString() || '',
      return_4: extracted.returns[3]?.toString() || '',
    });
    
    setIsOcrParsed(true);
  };

  // 🔥 HANDLE OCR PARSE - PAKAI ENDPOINT /api/parse-ocr
  const handleOcrParse = async (file: File) => {
    setImageStatus('uploading');
    setErrorMsg('');
    setLastResult(null);
    setShowRawOcr(false);
    setOcrParseResult(null);
    setIsOcrParsed(false);

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
      const response = await fetch(`${API_BASE}/api/parse-ocr`, {
        method: 'POST',
        headers: token ? { 'Authorization': `Bearer ${token}` } : {},
        body: formData,
      });

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || 'Failed to parse OCR');
      }

      const result: OcrParseResult = await response.json();
      setOcrParseResult(result);
      populateFormFromOcr(result);
      setImageStatus('success');
      setErrorMsg('OCR berhasil! Silakan periksa dan edit nilai yang salah, lalu klik "Proses Klasifikasi".');
      
    } catch (error: any) {
      console.error('OCR parse error:', error);
      setErrorMsg(error.message || 'Gagal memproses OCR.');
      setImageStatus('error');
    }
  };

  // 🔥 HANDLE KLASIFIKASI - KIRIM DATA KE /api/detect-manual
  const handleClassify = async () => {
    setImageStatus('uploading');
    setErrorMsg('');
    setLastResult(null);

    const loss4 = manualForm.loss_4 ? parseFloat(manualForm.loss_4) : null;

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
      loss_4: loss4,
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
      const response = await fetch(`${API_BASE}/api/detect-manual`, {
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
      setErrorMsg('');
      setIsOcrParsed(false);
      await fetchHistory();
      if (onDataChange) onDataChange();
    } catch (error: any) {
      console.error('Classification error:', error);
      setErrorMsg(error.message || 'Failed to connect to server.');
      setImageStatus('error');
    }
  };

  const { currentIndex, setCurrentIndex, totalData, autoPlay } = useSlide();
  const [prevTotalData, setPrevTotalData] = useState(0);

  // useEffect(() => {
  //   if (isLoadingHistory || allHistory.length === 0 || currentIndex < 0 || currentIndex >= allHistory.length) return;
  //   const currentRecord = allHistory[currentIndex];
  //   if (!currentRecord) return;

  //   const status = currentRecord.status || '';
    
  //   if (status.toLowerCase() === 'warning' || status.toLowerCase() === 'critical') {
  //     if (sentAlerts.has(currentRecord.id)) return;
      
  //     triggerSlideAlert(currentRecord.id)
  //       .then((res: { status: string }) => {
  //         if (res.status === 'sent') {
  //           setSentAlerts(prev => new Set(prev).add(currentRecord.id));
  //         }
  //       })
  //       .catch((err: any) => console.error('Error triggering slide alert:', err));
  //   }
  // }, [currentIndex, allHistory, isLoadingHistory, sentAlerts]);

  // useEffect(() => {
  //   if (!autoPlay || allHistory.length === 0) return;

  //   const interval = setInterval(() => {
  //     setCurrentIndex((prev: number) => {
  //       const newTotal = allHistory.length;
  //       if (newTotal > prevTotalData) {
  //         setPrevTotalData(newTotal);
  //         return newTotal - 1;
  //       }
  //       if (prev === newTotal - 1) {
  //         return 0;
  //       }
  //       return prev + 1;
  //     });
  //   }, 30000);

  //   return () => clearInterval(interval);
  // }, [autoPlay, allHistory.length, prevTotalData, setCurrentIndex]);

  const fetchHistory = async () => {
    setIsLoadingHistory(true);
    try {
      const token = localStorage.getItem('token');
      const response = await fetch(`${API_BASE}/api/history?limit=5000`, {
        headers: { 'Authorization': `Bearer ${token}` },
      });
      if (!response.ok) throw new Error('Failed to fetch history');
      const result = await response.json();

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
    await handleOcrParse(file);
  };

  const handleResetOcr = () => {
    setIsOcrParsed(false);
    setOcrParseResult(null);
    setPreview(null);
    setImageStatus('idle');
    setLastResult(null);
    setErrorMsg('');
  };

  const displayedHistory = [...allHistory].reverse();
  const progressPercent = totalData > 0 ? ((currentIndex + 1) / totalData) * 100 : 0;

  return (
    <div className="p-6 sm:p-8 bg-[#1e2f50] rounded-2xl border border-[#3b4f6e]"> 

        {/* Upload + Hasil */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <div className={`bg-[#1e2f50] p-6 sm:p-8 rounded-2xl border transition-all ${imageStatus === 'success' ? 'border-emerald-500/50 bg-emerald-500/5' :
            imageStatus === 'error' ? 'border-red-500/50' :
              imageStatus === 'uploading' ? 'border-blue-500/50 bg-blue-500/5' :
                'border-[#3b4f6e] hover:border-blue-500/50'
            }`}>
            <div className="flex bg-[#0f1a2e] p-1 rounded-xl mb-6 border border-[#3b4f6e]">
              <button
                type="button"
                onClick={() => {
                  setActiveInputMethod('ocr');
                  setImageStatus('idle');
                  setErrorMsg('');
                  setIsOcrParsed(false);
                  setOcrParseResult(null);
                  setLastResult(null);
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
                  setIsOcrParsed(false);
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
                <p className="text-xs text-slate-100 mb-5 text-center">Format: JPG, PNG</p>
                {/* 🔥 TAMBAHKAN: Peringatan/Note untuk OCR */}
<div className="mb-4 p-4 bg-amber-500/10 border border-amber-500/30 rounded-xl">
  <div className="flex items-start gap-2">
    <AlertTriangle size={16} className="text-amber-400 flex-shrink-0 mt-0.5" />
    <div>
      <p className="text-xs font-bold text-amber-400 uppercase tracking-wider">Tips Foto OTDR</p>
      <ul className="text-sm text-slate-300 mt-1 space-y-1 list-disc list-inside">
        <li><span className="text-white font-medium">Pastikan foto jelas dan tidak buram</span></li>
        <li><span className="text-white font-medium">Foto tidak boleh miring baik portrait maupun landscape</span></li>
        <li><span className="text-white font-medium">Pastikan seluruh tabel OTDR terlihat semua dalam frame</span></li>
        <li><span className="text-white font-medium">Hindari bayangan yang menutupi angka pada tabel</span></li>
        <li><span className="text-white font-medium">Pastikan cahaya cukup agar angka terbaca dengan baik</span></li>
      </ul>
    </div>
  </div>
</div>
                
                {isOcrParsed && ocrParseResult && (
                  <div className="mb-4 p-3 bg-emerald-500/10 border border-emerald-500/30 rounded-xl">
                    <p className="text-emerald-400 text-xs flex items-center gap-2">
                      <CheckCircle size={14} />
                      OCR berhasil! Silakan periksa dan edit nilai di bawah.
                    </p>
                    <p className="text-slate-100 text-[10px] mt-1">
                      Metode: {ocrParseResult.ocr_method || 'Unknown'}
                    </p>
                  </div>
                )}
                
                {preview && (
                  <div className="mb-4 rounded-xl overflow-hidden border border-[#3b4f6e] relative">
                    <img src={preview} alt="Preview OTDR" className="w-full h-40 object-cover" />
                    {isOcrParsed && (
                      <div className="absolute top-2 right-2 bg-emerald-500/80 text-white text-[10px] px-2 py-1 rounded-full flex items-center gap-1">
                        <CheckCircle size={10} /> Parsed
                      </div>
                    )}
                  </div>
                )}
                
                <div className="mb-5">
                  <label className="text-sm font-bold text-white mb-1.5 block">Input Prx Value (dBm)</label>
                  <div className="flex items-center gap-2">
                    <input
                      type="number"
                      step="0.1"
                      value={prxManual}
                      onChange={e => setPrxManual(e.target.value)}
                      className="flex-1 px-3 py-2 bg-[#0f1a2e] border border-[#3b4f6e] rounded-lg text-white text-sm focus:ring-2 focus:ring-blue-500/50 outline-none placeholder:text-slate-100"
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
                
                {!isOcrParsed ? (
                  <label
                    htmlFor="image-upload"
                    className={`w-full px-6 py-2.5 rounded-xl cursor-pointer flex items-center justify-center transition-all font-semibold text-white text-sm ${imageStatus === 'uploading' ? 'bg-slate-600 cursor-wait' : 'bg-blue-600 hover:bg-blue-500 shadow-lg shadow-blue-600/20'}`}
                  >
                    {imageStatus === 'uploading' ? 'Processing OCR...' : imageStatus === 'success' ? 'Upload Again' : imageStatus === 'error' ? 'Try Again' : 'Select OTDR Photo'}
                  </label>
                ) : (
                  <button
                    type="button"
                    onClick={handleResetOcr}
                    className="w-full px-6 py-2.5 rounded-xl bg-slate-600 hover:bg-slate-500 transition-all font-semibold text-white text-sm"
                  >
                    Upload Ulang
                  </button>
                )}
              </>
            ) : (
              // 🔥 TAB INPUT MANUAL - TETAP PERTAHANKAN
              <form onSubmit={(e) => { e.preventDefault(); handleClassify(); }} className="space-y-4">
                {isOcrParsed && ocrParseResult && (
                  <div className="p-3 bg-emerald-500/10 border border-emerald-500/30 rounded-xl mb-3">
                    <p className="text-emerald-400 text-xs flex items-center gap-2">
                      <CheckCircle size={14} />
                      Data dari OCR sudah diisi. Silakan periksa dan edit jika perlu.
                    </p>
                  </div>
                )}
                
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="text-[14px] font-bold text-white tracking-widest mb-1.5 block">Prx (dBm)</label>
                    <input type="number" step="0.01" value={manualForm.prx} onChange={e => setManualForm({ ...manualForm, prx: e.target.value })} placeholder="-15.60" className="w-full px-3 py-2 bg-[#0f1a2e] border border-[#3b4f6e] rounded-lg text-white text-xs focus:ring-2 focus:ring-blue-500/50 outline-none placeholder:text-slate-600 font-mono" />
                  </div>
                  <div>
                    <label className="text-[14px] font-bold text-white tracking-widest mb-1.5 block">Avg-Total (dB/km)</label>
                    <input type="number" step="0.001" value={manualForm.avg_total} onChange={e => setManualForm({ ...manualForm, avg_total: e.target.value })} placeholder="0.250" className="w-full px-3 py-2 bg-[#0f1a2e] border border-[#3b4f6e] rounded-lg text-white text-xs focus:ring-2 focus:ring-blue-500/50 outline-none placeholder:text-slate-100 font-mono" />
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
                          <td className="p-2 font-bold text-white text-xs">Km {km}</td>
                          <td className="p-2">
                            <input type="number" step="0.00001" value={manualForm[`distance_${km}` as keyof typeof manualForm]} onChange={e => setManualForm({ ...manualForm, [`distance_${km}`]: e.target.value })} placeholder="0.0" className="w-full px-1.5 py-1 bg-[#0f1a2e]/50 border border-[#3b4f6e] rounded text-white text-[11px] font-mono outline-none" />
                          </td>
                          <td className="p-2">
                            <input type="number" step="0.001" required={km !== 4} disabled={km === 4} value={km === 4 ? '' : manualForm[`loss_${km}` as keyof typeof manualForm]} onChange={e => setManualForm({ ...manualForm, [`loss_${km}`]: e.target.value })} placeholder={km === 4 ? '—' : '0.0'} className="w-full px-1.5 py-1 bg-[#0f1a2e]/50 border border-[#3b4f6e] rounded text-white text-[11px] font-mono outline-none disabled:opacity-45" />
                          </td>
                          <td className="p-2">
                            <input type="number" step="0.001" value={manualForm[`total_l_${km}` as keyof typeof manualForm]} onChange={e => setManualForm({ ...manualForm, [`total_l_${km}`]: e.target.value })} placeholder="0.0" className="w-full px-1.5 py-1 bg-[#0f1a2e]/50 border border-[#3b4f6e] rounded text-white text-[11px] font-mono outline-none" />
                          </td>
                          <td className="p-2">
                            <input type="number" step="0.001" value={manualForm[`avg_l_${km}` as keyof typeof manualForm]} onChange={e => setManualForm({ ...manualForm, [`avg_l_${km}`]: e.target.value })} placeholder="0.0" className="w-full px-1.5 py-1 bg-[#0f1a2e]/50 border border-[#3b4f6e] rounded text-white text-[11px] font-mono outline-none" />
                          </td>
                          <td className="p-2">
                            <input type="number" step="0.01" value={manualForm[`return_${km}` as keyof typeof manualForm]} onChange={e => setManualForm({ ...manualForm, [`return_${km}`]: e.target.value })} placeholder="0.0" className="w-full px-1.5 py-1 bg-[#0f1a2e]/50 border border-[#3b4f6e] rounded text-white text-[11px] font-mono outline-none" />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <button
                  type="submit"
                  disabled={imageStatus === 'uploading'}
                  className={`w-full px-6 py-2.5 rounded-xl flex items-center justify-center transition-all font-semibold text-white text-sm mt-4 gap-2 ${imageStatus === 'uploading' ? 'bg-slate-600 cursor-wait' : isOcrParsed ? 'bg-emerald-600 hover:bg-emerald-500 shadow-lg shadow-emerald-600/20' : 'bg-blue-600 hover:bg-blue-500 shadow-lg shadow-blue-600/20'}`}
                >
                  {imageStatus === 'uploading' ? (
                    <>
                      <RefreshCw size={16} className="animate-spin" />
                      Memproses...
                    </>
                  ) : (
                    <>
                      <Send size={16} />
                      {isOcrParsed ? 'Proses Klasifikasi (Data dari OCR)' : 'Proses Klasifikasi Manual'}
                    </>
                  )}
                </button>
                
                {isOcrParsed && (
                  <p className="text-[10px] text-emerald-400 text-center">
                    ✓ Menggunakan data dari OCR yang sudah diedit
                  </p>
                )}
              </form>
            )}
          </div>

          {/* 🔥 RESULT CLASSIFICATION - DENGAN DETECTED VALUES YANG BISA DIEDIT */}
          <div className="bg-[#1e2f50] p-8 rounded-2xl border border-[#3b4f6e]">
            <h3 className="text-lg font-bold text-white mb-4 uppercase tracking-widest">Result Classification</h3>
            
            {imageStatus === 'uploading' && (
              <div className="flex flex-col items-center justify-center h-52 gap-3">
                <RefreshCw className="w-8 h-8 text-blue-400 animate-spin" />
                <p className="text-sm text-slate-400">Processing...</p>
              </div>
            )}
            
            {imageStatus === 'idle' && !lastResult && !isOcrParsed && (
              <div className="flex flex-col items-center justify-center h-52 gap-2 text-slate-500">
                <Camera className="w-8 h-8" />
                <p className="text-sm italic">Upload photo or input manual data to view results</p>
              </div>
            )}
            
            {errorMsg && imageStatus !== 'success' && !lastResult && (
              <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-4 flex gap-3">
                <AlertTriangle className="w-5 h-5 text-red-400 flex-shrink-0 mt-0.5" />
                <p className="text-sm text-red-400">{errorMsg}</p>
              </div>
            )}

            {/* 🔥 TAMPILKAN DETECTED VALUES EDITABLE SAAT OCR PARSED (BELUM KLASIFIKASI) */}
{isOcrParsed && ocrParseResult && !lastResult && (
  <div className="space-y-3">
    <div className="flex justify-between items-center">
      <p className="text-[10px] font-bold text-slate-500 uppercase tracking-widest">DETECTED VALUES</p>
      <span className="text-[10px] text-emerald-400 flex items-center gap-1">
        <Edit3 size={10} /> Editable
      </span>
    </div>
    
    {/* Signal Power - Editable */}
    <div className="bg-blue-500/10 border border-blue-500/20 rounded-xl p-3 flex items-center justify-between">
      <div className="flex items-center gap-2">
        <Info size={14} className="text-blue-400" />
        <span className="text-xs text-blue-400 font-bold">Signal Power (Prx)</span>
      </div>
      <div className="flex items-center gap-1">
        <input
          type="text"
          inputMode="decimal"
          step="0.01"
          value={manualForm.prx}
          onChange={(e) => setManualForm({ ...manualForm, prx: e.target.value })}
          className="w-20 px-2 py-1 bg-[#0f1a2e] border border-[#3b4f6e] rounded text-white text-right font-mono text-[13px] outline-none focus:ring-1 focus:ring-blue-500/50"
        />
        <span className="text-white text-sm font-black">dBm</span>
        <span className="text-[10px] text-white ml-1">(input manual)</span>
      </div>
    </div>

    {/* Loss Values - Style Monitoring NOC */}
    <div className="grid grid-cols-2 gap-2 text-sm">
      {[1, 2, 3, 4].map((i) => (
        <div key={`loss-${i}`} className="bg-[#0f1a2e]/50 rounded-lg p-2 flex justify-between items-center hover:bg-[#0f1a2e] transition-colors">
          <span className="text-white font-medium">Loss Km {i} <span className="text-white text-[13px]">(dB)</span></span>
          <div className="flex items-center gap-1.5">
            <input
              type="text"
              inputMode="decimal"
              step="0.01"
              value={i === 4 ? (manualForm.loss_4 || '') : manualForm[`loss_${i}` as keyof typeof manualForm]}
              onChange={(e) => {
                const val = e.target.value;
                if (i === 4) {
                  setManualForm({ ...manualForm, loss_4: val });
                } else {
                  setManualForm({ ...manualForm, [`loss_${i}`]: val });
                }
              }}
              disabled={i === 4}
              placeholder={i === 4 ? '---' : '0.00'}
              className={`w-16 px-2 py-0.5 bg-[#0f1a2e] border border-[#3b4f6e] rounded text-white text-right font-mono text-[13px] outline-none focus:ring-1 focus:ring-blue-500/50 ${i === 4 ? 'opacity-45 cursor-not-allowed' : ''}`}
            />
          </div>
        </div>
      ))}
    </div>

    {/* Total-L Values */}
    <div className="grid grid-cols-2 gap-2 text-sm mt-1">
      {[1, 2, 3, 4].map((i) => (
        <div key={`total-${i}`} className="bg-[#0f1a2e]/50 rounded-lg p-2 flex justify-between items-center hover:bg-[#0f1a2e] transition-colors">
          <span className="text-white font-medium">Total-L Km {i} <span className="text-white text-[13px]">(dB)</span></span>
          <div className="flex items-center gap-1.5">
            <input
              type="text"
              inputMode="decimal"
              step="0.01"
              value={manualForm[`total_l_${i}` as keyof typeof manualForm]}
              onChange={(e) => setManualForm({ ...manualForm, [`total_l_${i}`]: e.target.value })}
              className="w-16 px-2 py-0.5 bg-[#0f1a2e] border border-[#3b4f6e] rounded text-white text-right font-mono text-[13px] outline-none focus:ring-1 focus:ring-blue-500/50"
            />
          </div>
        </div>
      ))}
    </div>

    {/* Avg-L Values */}
    <div className="grid grid-cols-2 gap-2 text-sm mt-1">
      {[1, 2, 3, 4].map((i) => (
        <div key={`avg-${i}`} className="bg-[#0f1a2e]/50 rounded-lg p-2 flex justify-between items-center hover:bg-[#0f1a2e] transition-colors">
          <span className="text-white font-medium">Avg-L Km {i} <span className="text-white text-[13px]">(dB/km)</span></span>
          <div className="flex items-center gap-1.5">
            <input
              type="text"
              inputMode="decimal"
              step="0.01"
              value={manualForm[`avg_l_${i}` as keyof typeof manualForm]}
              onChange={(e) => setManualForm({ ...manualForm, [`avg_l_${i}`]: e.target.value })}
              className="w-16 px-2 py-0.5 bg-[#0f1a2e] border border-[#3b4f6e] rounded text-white text-right font-mono text-[13px] outline-none focus:ring-1 focus:ring-blue-500/50"
            />
          </div>
        </div>
      ))}
    </div>

    {/* Avg-Total */}
    <div className="bg-[#0f1a2e]/70 rounded-lg p-2 flex justify-between items-center text-sm border border-[#3b4f6e]/50">
      <span className="text-white font-bold">Avg-Total<span className="text-white text-[13px] ml-1">(dB/km)</span></span>
      <div className="flex items-center gap-1.5">
        <input
          type="text"
          inputMode="decimal"
          step="0.01"
          value={manualForm.avg_total}
          onChange={(e) => setManualForm({ ...manualForm, avg_total: e.target.value })}
          className="w-16 px-2 py-0.5 bg-[#0f1a2e] border border-[#3b4f6e] rounded text-white text-right font-mono text-[13px] outline-none focus:ring-1 focus:ring-blue-500/50"
        />
      </div>
    </div>

    {/* Return Values */}
    <div className="grid grid-cols-2 gap-2 text-sm mt-1">
      {[1, 2, 3, 4].map((i) => (
        <div key={`ret-${i}`} className="bg-[#0f1a2e]/50 rounded-lg p-2 flex justify-between items-center hover:bg-[#0f1a2e] transition-colors">
          <span className="text-white font-medium">Return Km {i} <span className="text-white text-[13px]">(dB)</span></span>
          <div className="flex items-center gap-1.5">
            <input
              type="text"
              inputMode="decimal"
              step="0.01"
              value={manualForm[`return_${i}` as keyof typeof manualForm]}
              onChange={(e) => setManualForm({ ...manualForm, [`return_${i}`]: e.target.value })}
              className="w-16 px-2 py-0.5 bg-[#0f1a2e] border border-[#3b4f6e] rounded text-white text-right font-mono text-[13px] outline-none focus:ring-1 focus:ring-blue-500/50"
            />
          </div>
        </div>
      ))}
    </div>

    {/* Tombol Proses Klasifikasi */}
    <button
      onClick={handleClassify}
      disabled={imageStatus === 'uploading'}
      className="w-full mt-4 px-6 py-2.5 rounded-xl bg-emerald-600 hover:bg-emerald-500 transition-all font-semibold text-white text-sm flex items-center justify-center gap-2 shadow-lg shadow-emerald-600/20 disabled:opacity-50 disabled:cursor-not-allowed"
    >
      {imageStatus === 'uploading' ? (
        <>
          <RefreshCw size={16} className="animate-spin" />
          Memproses...
        </>
      ) : (
        <>
          <Send size={16} />
          Proses Klasifikasi
        </>
      )}
    </button>
    <p className="text-[12px] text-white text-center">
      Pastikan semua nilai sudah benar sebelum klasifikasi
    </p>
  </div>
)}

            {/* 🔥 TAMPILKAN HASIL KLASIFIKASI (SETELAH KLIK PROSES) */}
            {lastResult && imageStatus === 'success' && (
              <div className="space-y-4">
                <div className={`rounded-xl p-4 text-center border ${lastResult.status === 'Normal' ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30' : lastResult.status === 'Critical' ? 'bg-red-500/10 text-red-400 border-red-500/30' : 'bg-amber-500/10 text-amber-400 border-amber-500/30'}`}>
                  <p className="text-xs uppercase tracking-widest mb-1 opacity-70">Klasifikasi</p>
                  <p className="text-2xl font-black">{lastResult.prediction}</p>
                </div>
                <div className="bg-blue-500/10 border border-blue-500/20 rounded-xl p-3 flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Info size={14} className="text-blue-400" />
                    <span className="text-xs text-blue-400 font-bold">Signal Power (Prx)</span>
                  </div>
                  <div className="text-right">
                    <span className="text-white font-black text-sm">{lastResult.prx?.toString()} dBm</span>
                  </div>
                </div>
                <div className="space-y-2">
                  <p className="text-[12px] font-bold text-white uppercase tracking-widest">Detected Values</p>
                  <div className="grid grid-cols-2 gap-2 text-xs">
                    {lastResult.extracted.losses?.map((l: number | null, i: number) => (
                      <div key={`loss-${i}`} className="bg-[#0f1a2e] rounded-lg p-2 flex justify-between">
                        <span className="text-white">Loss Km {i + 1}</span>
                        <span className="text-white font-mono">{l === null || l === undefined || l === 0 ? '---' : l.toString()} dB</span>
                      </div>
                    ))}
                  </div>
                  {lastResult.extracted.total_ls && (
                    <div className="grid grid-cols-2 gap-2 text-xs mt-2">
                      {lastResult.extracted.total_ls.map((tl: number, i: number) => (
                        <div key={`total-${i}`} className="bg-[#0f1a2e] rounded-lg p-2 flex justify-between">
                          <span className="text-white">Total-L Km {i + 1}</span>
                          <span className="text-white font-mono">{tl === 0 || tl === null || tl === undefined ? '---' : tl.toString()} dB</span>
                        </div>
                      ))}
                    </div>
                  )}
                  {lastResult.extracted.avg_ls && (
                    <div className="grid grid-cols-2 gap-2 text-xs mt-2">
                      {lastResult.extracted.avg_ls.map((al: number, i: number) => (
                        <div key={`avg-${i}`} className="bg-[#0f1a2e] rounded-lg p-2 flex justify-between">
                          <span className="text-white">Avg-L Km {i + 1}</span>
                          <span className="text-white font-mono">{al === 0 ? '---' : al.toString()} dB/km</span>
                        </div>
                      ))}
                    </div>
                  )}
                  {lastResult.extracted.avg_total !== undefined && (
                    <div className="bg-[#0f1a2e] rounded-lg p-2 flex justify-between text-xs mt-2">
                      <span className="text-white">Avg-Total</span>
                      <span className="text-white font-mono">{lastResult.extracted.avg_total === 0 ? '---' : lastResult.extracted.avg_total.toFixed(2)} dB/km</span>
                    </div>
                  )}
                  <div className="grid grid-cols-2 gap-2 text-xs mt-2">
                    {lastResult.extracted.returns?.map((r: number, i: number) => (
                      <div key={`ret-${i}`} className="bg-[#0f1a2e] rounded-lg p-2 flex justify-between">
                        <span className="text-white">Return Km {i + 1}</span>
                        <span className="text-white font-mono">{r.toString()} dB</span>
                      </div>
                    ))}
                  </div>
                </div>
                <button onClick={() => setShowRawOcr(!showRawOcr)} className="text-[10px] text-slate-500 hover:text-slate-400 underline transition">
                  {showRawOcr ? '▲ Hide' : '▼ Show'} raw OCR text
                </button>
                {showRawOcr && (
                  <pre className="text-[9px] bg-[#0f1a2e] border border-[#3b4f6e] rounded-lg p-3 text-slate-400 overflow-x-auto max-h-32 whitespace-pre-wrap">
                    {lastResult.raw_text}
                  </pre>
                )}
              </div>
            )}
          </div>
        </div>

        {/* History Table */}
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
                <tr className="bg-[#1e2f50] text-white text-[13px] font-black tracking-widest border-b border-[#3b4f6e]">
                  <th className="px-6 py-4 text-center">TIME</th>
                  <th className="px-6 py-4 text-center">LOSS Km 1-4 (dB)</th>
                  <th className="px-6 py-4 text-center">TOTAL-L (dB)</th>
                  <th className="px-6 py-4 text-center">RETURN Km 1-4 (dB)</th>
                  <th className="px-6 py-4 text-center">Prx (dBm)</th>
                  <th className="px-6 py-4">CLASSIFICATION</th>
                  <th className="px-6 py-4 text-center">STATUS</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#3b4f6e]/50">
                {isLoadingHistory ? (
                  <tr><td colSpan={7} className="px-6 py-12 text-center text-slate-500"><RefreshCw size={18} className="animate-spin mx-auto" /></td></tr>
                ) : displayedHistory.length === 0 ? (
                  <tr><td colSpan={7} className="px-6 py-12 text-center text-slate-500 italic">No measurement history available.</td></tr>
                ) : (
                  displayedHistory.map((row, idx) => {
                    const displayTime = displayTimestamps[row.id];
                    let recordTime = '—';
                    if (displayTime) {
                      recordTime = formatDisplayTime(displayTime);
                    } else if (row.timestamp) {
                      recordTime = formatDisplayTime(row.timestamp);
                    }
                    const totalLValue = row.total_l_4;
                    const totalLDisplay = !totalLValue || totalLValue === 0 ? '---' : totalLValue.toFixed(2);
                    
                    return (
                      <tr key={row.id || idx} className="hover:bg-[#2a3d60]/20 transition-colors">
                        <td className="px-6 py-4 text-center text-white text-xs font-mono">{recordTime}</td>
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
  );
};

export default Detection;