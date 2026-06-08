// frontend/src/components/MainDashboard.tsx
import { useState, useEffect } from 'react';
import {
  Activity, CheckCircle2, AlertTriangle, Database, Clock, Network
} from 'lucide-react';
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, PieChart, Pie, Cell, ReferenceLine
} from 'recharts';
import { syncFromSheets } from '../services/api';
import topologyImage from '../assets/topology.png';
import { useSlide } from '../Context/SlideContext';
import NetworkTopology from '../components/NetworkTopology';

interface MainDashboardProps {
  refreshTrigger?: number;
  onDataChange?: () => void;
}

const COLORS = ['#10b981', '#f59e0b', '#3b82f6', '#ef4444', '#8b5cf6', '#ec4899'];

const StatusBadge = ({ status }: { status: string | null | undefined }) => {
  const cfg: Record<string, string> = {
    'Normal'  : 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
    'Warning' : 'bg-amber-500/15 text-amber-400 border-amber-500/30',
    'Critical': 'bg-red-500/15 text-red-400 border-red-500/30',
  };
  const dot: Record<string, string> = {
    'Normal'  : 'bg-emerald-400',
    'Warning' : 'bg-amber-400',
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

const getRealTimeLabels = (count: number) => {
  const now = new Date();
  const labels = [];
  for (let i = count - 1; i >= 0; i--) {
    const time = new Date(now.getTime() - i * 30 * 1000);
    labels.push(time.toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit', second: '2-digit' }));
  }
  return labels;
};

const MainDashboard = ({ refreshTrigger, onDataChange }: MainDashboardProps) => {
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [allData, setAllData] = useState<any[]>([]);
  
  const { 
    currentIndex, 
    setCurrentIndex, 
    totalData, 
    setTotalData,
    autoPlay, 
    setAutoPlay 
  } = useSlide();

  const [prevTotalData, setPrevTotalData] = useState(0);

  useEffect(() => {
    if (totalData !== prevTotalData) {
      setPrevTotalData(totalData);
    }
  }, [totalData, prevTotalData]);

  useEffect(() => {
    if (allData.length > 0 && currentIndex >= allData.length) {
      setCurrentIndex(0);
    }
  }, [allData.length, currentIndex, setCurrentIndex]);

  useEffect(() => {
    setTotalData(allData.length);
  }, [allData.length, setTotalData]);

  useEffect(() => {
    if (!autoPlay || allData.length === 0) return;
    const interval = setInterval(() => {
      setCurrentIndex((prev: number) => {
        const newTotal = allData.length;
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
  }, [autoPlay, allData.length, prevTotalData, setCurrentIndex]);

  const fetchAllData = async () => {
    try {
      const token = localStorage.getItem('token');
      if (!token) return;

      const response = await fetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/dashboard?limit=5000`, {
        headers: { 'Authorization': `Bearer ${token}` },
      });
      const dashboard = await response.json();

      if (dashboard?.data?.length > 0) {
        const sorted = [...dashboard.data].sort(
          (a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime()
        );
        setAllData(sorted);
      } else {
        setAllData([]);
        setCurrentIndex(0);
      }
    } catch (error) {
      console.error('Fetch error:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { 
    fetchAllData(); 
  }, []);

  useEffect(() => {
    if (refreshTrigger && refreshTrigger > 0) {
      fetchAllData();
    }
  }, [refreshTrigger]);

  const handleSync = async () => {
    setSyncing(true);
    try {
      const result = await syncFromSheets();
      alert(`Sync selesai! ${result.saved} baris berhasil`);
      await fetchAllData();
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
        <p className="text-white text-sm">Loading...</p>
      </div>
    );
  }

  const currentRecord = allData[currentIndex];
  const progressPercent = totalData > 0 ? ((currentIndex + 1) / totalData) * 100 : 0;

  const processedData = allData.slice(0, currentIndex + 1);
  const totalProcessed = processedData.length;

  const faultMap: Record<string, number> = {};
  processedData.forEach((r: any) => {
    const k = r.klasifikasi || 'Unknown';
    faultMap[k] = (faultMap[k] || 0) + 1;
  });
  const faultDistribution = Object.entries(faultMap).map(([name, value]) => ({ name, value }));

  const normalCount = processedData.filter(d => d.klasifikasi === 'Normal').length;
  const gangguanCount = totalProcessed - normalCount;

  const isFiberCut = () => {
    if (!currentRecord) return false;
    const klasifikasi = currentRecord.klasifikasi || '';
    return klasifikasi.toLowerCase() === 'fiber cut';
  };

  const getFiberCutKM = () => {
    if (!isFiberCut()) return -1;
    const losses = [
      currentRecord?.loss_1 || 0,
      currentRecord?.loss_2 || 0,
      currentRecord?.loss_3 || 0,
      currentRecord?.loss_4 || 0
    ];
    const cutIndex = losses.findIndex(loss => loss === 0);
    return cutIndex !== -1 ? cutIndex + 1 : 4;
  };

  const isGangguan = currentRecord?.klasifikasi && currentRecord.klasifikasi !== 'Normal';

  const tableData = allData.slice(Math.max(0, currentIndex - 4), currentIndex + 1).reverse();
  const realTimeLabels15 = getRealTimeLabels(15);
  const realTimeLabels6 = getRealTimeLabels(6);

  // 🔥 PERUBAHAN 1: PRx chart hanya 6 titik (sama seperti loss/return)
  const chartData = allData.slice(Math.max(0, currentIndex - 5), currentIndex + 1).map((r, idx) => ({
    time: realTimeLabels6[idx],
    prx: r.prx || -14,
  }));

  const miniChartData = allData.slice(Math.max(0, currentIndex - 5), currentIndex + 1).map((r, idx) => ({
    time: realTimeLabels6[idx],
    loss_1: r.loss_1 || 0,
    loss_2: r.loss_2 || 0,
    loss_3: r.loss_3 || 0,
    loss_4: r.loss_4 || 0,
    return_1: r.return_1 || 0,
    return_2: r.return_2 || 0,
    return_3: r.return_3 || 0,
    return_4: r.return_4 || 0,
    prx: r.prx || -14,
  }));

  const lossColors = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444'];
  const returnColors = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444'];

  const getLossChartData = (km: number) => miniChartData.map(i => ({ time: i.time, value: (i as any)[`loss_${km}`] }));
  const getReturnChartData = (km: number) => miniChartData.map(i => ({ time: i.time, value: (i as any)[`return_${km}`] }));

  const LOSS_THRESHOLD = 1.2;
  const PRX_THRESHOLD = -24;
  const RETURN_THRESHOLD = -30;
  const LOSS_Y_DOMAIN = [0, 2];
  const RETURN_Y_DOMAIN = [-60, -20];
  const PRX_Y_DOMAIN = [-30, -10];

  return (
    <div className="min-h-screen bg-[#14213d] text-slate-300 font-sans pb-20 w-full">
      <main className="p-6 w-full space-y-10">
        {/* Progress + Sync */}
        <div className="flex justify-between items-center gap-4">
          <div className="flex-1 bg-[#1e2f50] border border-[#3b4f6e] rounded-2xl p-4">
            <div className="flex justify-between items-center mb-2">
              <span className="text-xs text-white">Slide Show Progress (Classification)</span>
              <div className="flex items-center gap-3">
                <span className="text-xs text-white font-mono">{currentIndex + 1} / {totalData}</span>
                {/* <button
                  onClick={() => setAutoPlay(!autoPlay)}
                  className={`px-3 py-1 rounded-lg text-[10px] font-bold transition ${
                    autoPlay ? 'bg-emerald-600 text-white' : 'bg-slate-600 text-slate-300'
                  }`}
                >
                  {autoPlay ? '⏸ Pause' : '▶ Play'}
                </button> */}
              </div>
            </div>
            <div className="w-full bg-slate-600 rounded-full h-2">
              <div
                className="bg-emerald-500 h-2 rounded-full transition-all duration-500"
                style={{ width: `${progressPercent}%` }}
              />
            </div>
            {/* <p className="text-[10px] text-slate-400 mt-2 text-center">
              {autoPlay
                ? '⏳ Auto-slide: bergerak setiap 30 detik dari data pertama ke terakhir'
                : '⏸ Paused - klik Play untuk lanjut'}
            </p> */}
          </div>
          {/* <button
            onClick={handleSync}
            disabled={syncing}
            className="px-4 py-2 bg-emerald-600 hover:bg-emerald-700 disabled:bg-slate-600
              rounded-xl text-xs font-bold uppercase flex items-center gap-2 whitespace-nowrap"
          >
            {syncing ? 'Syncing...' : 'Sync from Sheets'}
          </button> */}
        </div>

        {/* Summary Stats */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          <div className="bg-[#1e2f50] border border-[#3b4f6e] p-6 rounded-[1.8rem] flex justify-between items-center">
            <div className="flex items-center gap-4"><div className="p-2.5 rounded-xl bg-blue-500/20 text-blue-400"><Activity size={20} /></div><span className="text-md text-white">Total Pengukuran</span></div>
            <span className="text-4xl font-black text-blue-400">{totalProcessed}</span>
          </div>
          <div className="bg-[#1e2f50] border border-[#3b4f6e] p-6 rounded-[1.8rem] flex justify-between items-center">
            <div className="flex items-center gap-4"><div className="p-2.5 rounded-xl bg-emerald-500/20 text-emerald-400"><CheckCircle2 size={20} /></div><span className="text-md text-white">Normal</span></div>
            <span className="text-4xl font-black text-emerald-400">{normalCount}</span>
          </div>
          <div className="bg-[#1e2f50] border border-[#3b4f6e] p-6 rounded-[1.8rem] flex justify-between items-center">
            <div className="flex items-center gap-4"><div className="p-2.5 rounded-xl bg-amber-500/20 text-amber-400"><AlertTriangle size={20} /></div><span className="text-md text-white">Terdeteksi Gangguan</span></div>
            <span className="text-4xl font-black text-amber-400">{gangguanCount}</span>
          </div>
        </div>

        {/* Loss per KM */}
        <section className="space-y-4">
          <div className="flex justify-between items-center px-2">
            <h3 className="text-sm font-black text-white uppercase tracking-[0.2em]">Loss per KM (dB)</h3>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-6">
            {[1, 2, 3, 4].map(km => {
              const lossValue = currentRecord?.[`loss_${km}`] || 0;
              const lossChartData = getLossChartData(km);
              const isAboveThreshold = lossValue > LOSS_THRESHOLD;
              const cutKM = getFiberCutKM();
              const isCut = isFiberCut() && km >= cutKM;
              
              if (isCut) {
                return (
                  <div key={km} className="group bg-[#1e2f50] border border-red-500/30 p-5 rounded-[2rem] opacity-70">
                    <div className="text-center py-8">
                      <p className="text-red-400 text-xs font-bold animate-pulse">⚠️ FIBER CUT</p>
                      <p className="text-slate-400 text-[10px] mt-1">Sinyal terputus di KM {cutKM}</p>
                      <p className="text-slate-500 text-[8px] mt-2">Prx: {currentRecord?.prx} dBm</p>
                    </div>
                  </div>
                );
              }
              
              return (
                <div key={km} className="group bg-[#1e2f50] border border-[#3b4f6e] p-5 rounded-[2rem] hover:border-blue-500/50 transition-all shadow-xl relative overflow-hidden">
                  <div className="absolute -top-10 -right-10 w-32 h-32 blur-[80px] opacity-10 rounded-full" style={{ backgroundColor: lossColors[km - 1] }} />
                  <div className="flex justify-between items-start mb-3 relative z-10">
                    <div>
                      <p className="text-[13px] font-black text-white uppercase tracking-widest mb-1">Loss KM {km}</p>
                      <div className="flex items-baseline gap-1">
                        <h4 className={`text-2xl font-black leading-none ${isAboveThreshold ? 'text-red-400' : 'text-white'}`}>
                          {lossValue === 0 && km === 4 ? '---' : lossValue}
                        </h4>
                        {!(lossValue === 0 && km === 4) && <span className="text-[10px] font-bold text-white uppercase">dB</span>}
                      </div>
                      {isAboveThreshold && <p className="text-[7px] text-red-400 mt-1 animate-pulse">⚠️ Melebihi batas!</p>}
                    </div>
                    <div className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: lossColors[km - 1], boxShadow: `0 0 12px ${lossColors[km - 1]}` }} />
                  </div>
                  <div className="h-[200px] w-full relative z-10">
                    <ResponsiveContainer width="100%" height="100%">
                      <AreaChart data={lossChartData} margin={{ top: 10, right: 10, left: 0, bottom: 15 }}>
                        <defs>
                          <linearGradient id={`lossGrad${km}`} x1="0" y1="0" x2="0" y2="1">
                            <stop offset="5%" stopColor={lossColors[km - 1]} stopOpacity={0.3} />
                            <stop offset="95%" stopColor={lossColors[km - 1]} stopOpacity={0} />
                          </linearGradient>
                        </defs>
                        <XAxis 
                          dataKey="time" 
                          stroke="#ffffff"
                          fontSize={10} 
                          tickLine={false} 
                          axisLine={{ stroke: '#ffffff', strokeWidth: 1 }}
                          dy={8} 
                          tick={{ fill: '#ffffff' }}
                          label={{ value: 'Waktu', position: 'insideBottom', offset: -5, fill: '#ffffff', fontSize: 10 }} 
                        />
                        <YAxis 
                    stroke="#ffffff"
                    fontSize={11} 
                    tickLine={false} 
                    axisLine={{ stroke: '#ffffff', strokeWidth: 1 }}
                    domain={LOSS_Y_DOMAIN} 
                    tick={{ fill: '#ffffff' }}
                    label={{ value: 'Loss (dB)', angle: -90, position: 'insideLeft', fill: '#ffffff', fontSize: 11, dx: 3 }} 
                  />
                        <CartesianGrid strokeDasharray="3 3" stroke="#3d4e6b" vertical={false} />
                        <ReferenceLine y={LOSS_THRESHOLD} stroke="#ef4444" strokeDasharray="3 3" strokeWidth={1.5} />
                        <Area type="monotone" dataKey="value" stroke={lossColors[km - 1]} strokeWidth={2} fill={`url(#lossGrad${km})`} dot={{ r: 2, fill: lossColors[km - 1], strokeWidth: 1, stroke: '#1e2f50' }} />
                      </AreaChart>
                    </ResponsiveContainer>
                  </div>
                </div>
              );
            })}
          </div>
        </section>

        {/* Return Loss per KM */}
        <section className="space-y-4">
          <div className="flex justify-between items-center px-2">
            <h3 className="text-sm font-black text-white uppercase tracking-[0.2em]">Return Loss per KM (dB)</h3>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-6">
            {[1, 2, 3, 4].map(km => {
              const returnValue = currentRecord?.[`return_${km}`] || 0;
              const returnChartData = getReturnChartData(km);
              const isAboveThreshold = returnValue > RETURN_THRESHOLD;
              const cutKM = getFiberCutKM();
              const isCut = isFiberCut() && km >= cutKM;
              
              if (isCut) {
                return (
                  <div key={km} className="group bg-[#1e2f50] border border-red-500/30 p-5 rounded-[2rem] opacity-70">
                    <div className="text-center py-8">
                      <p className="text-red-400 text-xs font-bold animate-pulse">⚠️ FIBER CUT</p>
                      <p className="text-slate-400 text-[10px] mt-1">Sinyal terputus di KM {cutKM}</p>
                    </div>
                  </div>
                );
              }
              
              return (
                <div key={km} className="group bg-[#1e2f50] border border-[#3b4f6e] p-5 rounded-[2rem] hover:border-blue-500/50 transition-all shadow-xl relative overflow-hidden">
                  <div className="absolute -top-10 -right-10 w-32 h-32 blur-[80px] opacity-10 rounded-full" style={{ backgroundColor: returnColors[km - 1] }} />
                  <div className="flex justify-between items-start mb-3 relative z-10">
                    <div>
                      <p className="text-[13px] font-black text-white uppercase tracking-widest mb-1">Return KM {km}</p>
                      <div className="flex items-baseline gap-1">
                        <h4 className={`text-2xl font-black leading-none ${isAboveThreshold ? 'text-red-400' : 'text-white'}`}>{returnValue}</h4>
                        <span className="text-[10px] font-bold text-white uppercase">dB</span>
                      </div>
                      {isAboveThreshold && <p className="text-[7px] text-red-400 mt-1 animate-pulse">⚠️ Melebihi batas!</p>}
                    </div>
                    <div className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: returnColors[km - 1], boxShadow: `0 0 12px ${returnColors[km - 1]}` }} />
                  </div>
                  <div className="h-[200px] w-full relative z-10">
                    <ResponsiveContainer width="100%" height="100%">
                      <AreaChart data={returnChartData} margin={{ top: 10, right: 10, left: 0, bottom: 15 }}>
                        <defs>
                          <linearGradient id={`returnGrad${km}`} x1="0" y1="0" x2="0" y2="1">
                            <stop offset="5%" stopColor={returnColors[km - 1]} stopOpacity={0.3} />
                            <stop offset="95%" stopColor={returnColors[km - 1]} stopOpacity={0} />
                          </linearGradient>
                        </defs>
                        <XAxis 
                          dataKey="time" 
                          stroke="#ffffff"
                          fontSize={10} 
                          tickLine={false} 
                          axisLine={{ stroke: '#ffffff', strokeWidth: 1 }}
                          dy={8} 
                          tick={{ fill: '#ffffff' }}
                          label={{ value: 'Waktu', position: 'insideBottom', offset: -5, fill: '#ffffff', fontSize: 10 }} 
                        />
                        <YAxis 
                    stroke="#ffffff"
                    fontSize={11} 
                    tickLine={false} 
                    axisLine={{ stroke: '#ffffff', strokeWidth: 1 }}
                    domain={RETURN_Y_DOMAIN} 
                    tick={{ fill: '#ffffff' }}
                    label={{ value: 'Return (dB)', angle: -90, position: 'insideLeft', fill: '#ffffff', fontSize: 11, dx: 3 }} 
                  />
                        <CartesianGrid strokeDasharray="3 3" stroke="#3d4e6b" vertical={false} />
                        <Tooltip contentStyle={{ backgroundColor: '#1e2f50', border: '1px solid #3b4f6e', borderRadius: '8px', fontSize: '10px' }} />
                        <ReferenceLine y={RETURN_THRESHOLD} stroke="#ef4444" strokeDasharray="3 3" strokeWidth={1.5} />
                        <Area type="monotone" dataKey="value" stroke={returnColors[km - 1]} strokeWidth={2}
                          fill={`url(#returnGrad${km})`}
                          dot={{ r: 2, fill: returnColors[km - 1], strokeWidth: 1, stroke: '#1e2f50' }} />
                      </AreaChart>
                    </ResponsiveContainer>
                  </div>
                </div>
              );
            })}
          </div>
        </section>

        {/* Signal Power + Pie Chart */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2 bg-[#1e2f50] border border-[#3b4f6e] rounded-[2.5rem] p-8">
            <div className="flex justify-between items-start mb-6">
              <div>
                <h3 className="text-sm font-black text-white uppercase tracking-widest flex items-center gap-2">
                  <div className="w-1.5 h-4 bg-blue-500 rounded-full" />
                  Signal Power (Prx) Monitoring
                </h3>
                <div className="mt-3">
                  <p className="text-2xl text-white font-black leading-none ${isAboveThreshold ? 'text-red-400' : 'text-white'}">
                    {currentRecord?.prx || '—'} <span className="text-sm text-white">dBm</span>
                  </p>
                </div>
              </div>
            </div>
            <div className="h-[320px] w-full">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={chartData} margin={{ top: 10, right: 20, left: 0, bottom: 20 }}>
                  <defs>
                    <linearGradient id="colorPrx" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.3} />
                      <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#3d4e6b" vertical={false} />
                  <XAxis 
                    dataKey="time" 
                    stroke="#ffffff"
                    fontSize={11} 
                    tickLine={false} 
                    axisLine={{ stroke: '#ffffff', strokeWidth: 1 }}
                    dy={8} 
                    tick={{ fill: '#ffffff' }}
                    label={{ value: 'Waktu', position: 'insideBottom', offset: -5, fill: '#ffffff', fontSize: 11 }} 
                  />
                  <YAxis 
                    stroke="#ffffff"
                    fontSize={11} 
                    tickLine={false} 
                    axisLine={{ stroke: '#ffffff', strokeWidth: 1 }}
                    domain={PRX_Y_DOMAIN} 
                    tick={{ fill: '#ffffff' }}
                    label={{ value: 'PRX (dBm)', angle: -90, position: 'insideLeft', fill: '#ffffff', fontSize: 11, dx: 3 }} 
                  />
                  <Tooltip contentStyle={{ backgroundColor: '#1e2f50', border: '1px solid #3b4f6e', borderRadius: '8px' }} />
                  <ReferenceLine y={PRX_THRESHOLD} stroke="#ef4444" strokeDasharray="3 3" strokeWidth={2} />
                  <Area type="monotone" dataKey="prx" stroke="#3b82f6" strokeWidth={3} fill="url(#colorPrx)" dot={{ r: 4, fill: '#3b82f6', strokeWidth: 2, stroke: '#1e2f50' }} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>

          <div className="bg-[#1e2f50] border border-[#3b4f6e] rounded-[2.5rem] p-8 flex flex-col">
            <h3 className="text-sm font-black text-white uppercase tracking-widest mb-4 flex items-center gap-2">
              <div className="w-1.5 h-4 bg-emerald-500 rounded-full" />
              Fault Distribution
            </h3>
            <div className="flex-1 min-h-[250px]">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie data={faultDistribution} innerRadius={60} outerRadius={85} paddingAngle={5} dataKey="value" stroke="none">
                    {faultDistribution.map((_, index) => (<Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />))}
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

        {/* Network Topology Section */}
        <section className="space-y-4">
          <div className="flex justify-between items-center px-2">
            <h3 className="text-sm font-black text-white uppercase tracking-[0.2em]">
              Fiber Optic Topology
            </h3>
            {isGangguan && (
              <div className={`flex items-center gap-2 px-3 py-1 rounded-full ${
                isFiberCut() 
                  ? 'bg-red-500/10' 
                  : 'bg-amber-500/10'
              }`}>
                <div className={`w-2 h-2 rounded-full animate-pulse ${
                  isFiberCut() ? 'bg-red-500' : 'bg-amber-500'
                }`} />
                <span className={`text-[10px] font-bold ${
                  isFiberCut() ? 'text-red-400' : 'text-amber-400'
                }`}>
                  {isFiberCut() ? `⚠️ FIBER CUT` : `⚠️ ${currentRecord?.klasifikasi} DETECTED`}
                </span>
              </div>
            )}
          </div>
          
          <NetworkTopology
            losses={[
              currentRecord?.loss_1 || 0,
              currentRecord?.loss_2 || 0,
              currentRecord?.loss_3 || 0,
              currentRecord?.loss_4 || 0
            ]}
            prx={currentRecord?.prx || -14}
            klasifikasi={currentRecord?.klasifikasi || 'Normal'}
            status={currentRecord?.status || 'Normal'}
            cutKM={getFiberCutKM()}
            currentRecord={currentRecord}
          />
        </section>

        {/* Prediction Results Table */}
<div className="bg-[#1e2f50] border border-[#3b4f6e] rounded-[2.5rem] p-8 w-full shadow-2xl overflow-hidden">
  <div className="flex justify-between items-center mb-8">
    <h3 className="text-sm font-black text-white uppercase tracking-widest flex items-center gap-2">
      <div className="w-1.5 h-4 bg-indigo-500 rounded-full" />
      Prediction Results Table
    </h3>
    <div className="flex items-center gap-2 text-slate-400">
    </div>
  </div>
  <div className="overflow-x-auto">
    <table className="w-full text-left">
      <thead>
        <tr className="border-b border-[#3b4f6e] text-white text-[13px] font-black uppercase tracking-widest">
          <th className="pb-4 px-4">Waktu</th>
          <th className="pb-4 px-4 text-center">Loss KM1-4 (dB)</th>
          <th className="pb-4 px-4 text-center">Total-L (dB)</th>
          <th className="pb-4 px-4 text-center">Return KM1-4 (dB)</th>
          <th className="pb-4 px-4 text-center">PRX (dBm)</th>
          <th className="pb-4 px-4">Klasifikasi</th>
          <th className="pb-4 px-4 text-center">Status</th>
        </tr>
      </thead>
      <tbody>
        {tableData.length > 0 ? tableData.map((row, idx) => {
          const timeOffset = (tableData.length - 1 - idx) * 30;
          const realTime = new Date(Date.now() - timeOffset * 1000).toLocaleString('id-ID');
          const loss4Display = (row.loss_4 || 0) === 0 ? '---' : (row.loss_4 || 0);
          // 🔥 Total-L hanya menampilkan nilai di KM4 (total_l_4)
          const totalLDisplay = (row.total_l_4 || 0) === 0 ? '---' : (row.total_l_4 || 0);
          return (
            <tr key={idx} className="border-b border-[#3b4f6e]/50 hover:bg-[#2a3d60]/20 transition-colors">
              <td className="py-5 px-4 text-slate-300 text-xs">{realTime}</td>
              <td className="py-5 px-4 text-center text-white text-xs font-mono">
                {(row.loss_1 || 0)} / {(row.loss_2 || 0)} / {(row.loss_3 || 0)} / {loss4Display}
              </td>
              {/* 🔥 Total-L hanya 1 nilai (total_l_4) */}
              <td className="py-5 px-4 text-center text-white text-xs font-mono">
                {totalLDisplay}
              </td>
              <td className="py-5 px-4 text-center text-white text-xs font-mono">
                {(row.return_1 || 0)} / {(row.return_2 || 0)} / {(row.return_3 || 0)} / {(row.return_4 || 0)}
              </td>
              <td className="py-5 px-4 text-center text-blue-400 font-bold text-xs font-mono">
                {row.prx || '—'} dBm
              </td>
              <td className="py-5 px-4">
                <span className={`px-3 py-1 rounded-full text-[11px] font-black border ${
                  row.klasifikasi === 'Normal' ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30' :
                  row.klasifikasi === 'Warning' ? 'bg-amber-500/20 text-amber-400 border-amber-500/30' :
                  row.klasifikasi === 'Fiber Cut' ? 'bg-red-500/20 text-red-400 border-red-500/30 animate-pulse' :
                  'bg-amber-500/20 text-amber-400 border-amber-500/30'
                }`}>
                  {row.klasifikasi || 'Unknown'}
                </span>
              </td>
              <td className="py-5 px-4 text-center"><StatusBadge status={row.status} /></td>
            </tr>
          );
        }) : (
          <tr><td colSpan={7} className="py-10 text-center text-slate-500 italic">Belum ada data</td></tr>
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