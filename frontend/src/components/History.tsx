// frontend/src/components/History.tsx
import { useState, useEffect } from 'react';
import { 
  Search, 
  Calendar, 
  Download, 
  RefreshCw,
  ChevronLeft,
  ChevronRight,
  X
} from 'lucide-react';

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
  source: string | null;
}

interface HistoryProps {
  refreshTrigger?: number;
}

const API_BASE = import.meta.env.VITE_API_URL || 'https://optim-api-ckfhb5heg3f3btgz.southeastasia-01.azurewebsites.net';

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

const History = ({ refreshTrigger }: HistoryProps) => {
  const [allData, setAllData] = useState<HistoryRow[]>([]);
  const [filteredData, setFilteredData] = useState<HistoryRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState('');
  const [filterStatus, setFilterStatus] = useState<string>('all');
  const [filterKlasifikasi, setFilterKlasifikasi] = useState<string>('all');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [currentPage, setCurrentPage] = useState(1);
  const [itemsPerPage, setItemsPerPage] = useState(50);
  const [sharedSlideIndex, setSharedSlideIndex] = useState<number | null>(null);

  // Poll shared slide index dari backend (posisi slide dashboard)
  useEffect(() => {
    const fetchSharedSlide = async () => {
      try {
        const token = localStorage.getItem('token');
        const res = await fetch(`${API_BASE}/api/shared-slide`, {
          headers: token ? { 'Authorization': `Bearer ${token}` } : {},
        });
        if (!res.ok) return;
        const json = await res.json();
        setSharedSlideIndex(json.current_index ?? null);
      } catch {
        // silent
      }
    };
    fetchSharedSlide();
    const interval = setInterval(fetchSharedSlide, 5000);
    return () => clearInterval(interval);
  }, []);

  // Ambil data dari API
  const fetchHistory = async () => {
    setLoading(true);
    try {
      const token = localStorage.getItem('token');
      const response = await fetch(`${API_BASE}/api/history?limit=10000`, {
        headers: { 'Authorization': `Bearer ${token}` },
      });
      if (!response.ok) throw new Error('Failed to fetch history');
      const result = await response.json();

      const mappedData = (result.history || []).map((record: any) => ({
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
        source: record.source,
      }));

      // Sort by timestamp DESC (terbaru di atas)
      const sorted = [...mappedData].sort(
        (a, b) => new Date(b.timestamp || 0).getTime() - new Date(a.timestamp || 0).getTime()
      );
      setAllData(sorted);
      setFilteredData(sorted);
    } catch (err) {
      console.error('History fetch error:', err);
    } finally {
      setLoading(false);
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

  // Filter data
  useEffect(() => {
    // Batasi data sesuai posisi slide dashboard (slide ke-N = tampilkan N data pertama)
    // Data diurutkan DESC (terbaru di atas), slide bergerak dari data terlama ke terbaru
    // jadi "N data pertama" = N data terlama = slice dari belakang array DESC
    let data = [...allData];
    if (sharedSlideIndex !== null) {
      const limit = sharedSlideIndex + 1; // index 0-based → jumlah data
      // allData sudah sort DESC, data ke-1 s/d ke-N dari urutan ASC = elemen terakhir N dari DESC
      data = data.slice(Math.max(0, data.length - limit));
    }

    // Search
    if (searchTerm.trim()) {
      const term = searchTerm.toLowerCase();
      data = data.filter(row => 
        row.klasifikasi?.toLowerCase().includes(term) ||
        row.status?.toLowerCase().includes(term) ||
        row.source?.toLowerCase().includes(term) ||
        row.id?.toString().includes(term)
      );
    }

    // Filter Status
    if (filterStatus !== 'all') {
      data = data.filter(row => row.status?.toLowerCase() === filterStatus.toLowerCase());
    }

    // Filter Klasifikasi
    if (filterKlasifikasi !== 'all') {
      data = data.filter(row => row.klasifikasi?.toLowerCase() === filterKlasifikasi.toLowerCase());
    }

    // Filter Tanggal
    if (startDate) {
      data = data.filter(row => {
        if (!row.timestamp) return false;
        const rowDate = new Date(row.timestamp).toISOString().split('T')[0];
        return rowDate >= startDate;
      });
    }
    if (endDate) {
      data = data.filter(row => {
        if (!row.timestamp) return false;
        const rowDate = new Date(row.timestamp).toISOString().split('T')[0];
        return rowDate <= endDate;
      });
    }

    setFilteredData(data);
    setCurrentPage(1);
  }, [allData, searchTerm, filterStatus, filterKlasifikasi, startDate, endDate, sharedSlideIndex]);

  // Format timestamp
  const formatTimestamp = (timestamp: string | null) => {
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

  const formatLossValue = (value: number | null | undefined) => {
    if (value === null || value === undefined || value === 0) return '---';
    return value.toFixed(2);
  };

  const formatReturnValue = (value: number | null | undefined) => {
    if (value === null || value === undefined) return '---';
    return value.toFixed(1);
  };

  // Export CSV
  const exportCSV = () => {
    if (filteredData.length === 0) {
      alert('Tidak ada data untuk diexport');
      return;
    }

    const headers = ['ID', 'Timestamp', 'PRX (dBm)', 'Loss 1', 'Loss 2', 'Loss 3', 'Loss 4', 
                     'Total-L 4', 'Return 1', 'Return 2', 'Return 3', 'Return 4', 
                     'Klasifikasi', 'Status', 'Source'];
    
    const rows = filteredData.map(row => [
      row.id,
      formatTimestamp(row.timestamp),
      row.prx?.toFixed(2) || '—',
      formatLossValue(row.loss_1),
      formatLossValue(row.loss_2),
      formatLossValue(row.loss_3),
      formatLossValue(row.loss_4),
      formatLossValue(row.total_l_4),
      formatReturnValue(row.return_1),
      formatReturnValue(row.return_2),
      formatReturnValue(row.return_3),
      formatReturnValue(row.return_4),
      row.klasifikasi || '—',
      row.status || '—',
      row.source || '—',
    ]);

    const csvContent = [
      headers.join(','),
      ...rows.map(row => row.join(','))
    ].join('\n');

    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = `history_${new Date().toISOString().split('T')[0]}.csv`;
    link.click();
    URL.revokeObjectURL(link.href);
  };

  // Reset filters
  const resetFilters = () => {
    setSearchTerm('');
    setFilterStatus('all');
    setFilterKlasifikasi('all');
    setStartDate('');
    setEndDate('');
  };

  // Pagination
  const totalPages = Math.ceil(filteredData.length / itemsPerPage);
  const startIndex = (currentPage - 1) * itemsPerPage;
  const paginatedData = filteredData.slice(startIndex, startIndex + itemsPerPage);

  // Get unique klasifikasi for filter
  const klasifikasiOptions = [...new Set(allData.map(r => r.klasifikasi).filter(Boolean))];

  return (
    <div className="p-6 sm:p-8 bg-[#1e2f50] rounded-2xl border border-[#3b4f6e]">
      
      {/* Header */}
      <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4 mb-6">
        <h2 className="text-lg font-bold text-white uppercase tracking-widest flex items-center gap-3">
          <div className="w-1.5 h-6 bg-indigo-500 rounded-full" />
          History of Measurements
          <span className="text-xs text-slate-400 bg-[#0f1a2e] px-3 py-1 rounded-full border border-[#3b4f6e]">
            {filteredData.length} data
          </span>
          {sharedSlideIndex !== null && (
            <span className="text-xs text-blue-400 bg-blue-500/10 px-3 py-1 rounded-full border border-blue-500/20">
              Slide {sharedSlideIndex + 1}
            </span>
          )}
        </h2>
        <button
          onClick={fetchHistory}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-500 rounded-xl text-xs font-bold text-white flex items-center gap-2 transition"
        >
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          Refresh
        </button>
      </div>

      {/* Filter Section */}
      <div className="bg-[#0f1a2e] rounded-xl p-4 border border-[#3b4f6e] mb-6">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          
          {/* Search */}
          <div className="relative">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
            <input
              type="text"
              placeholder="Cari ID, Klasifikasi, Status..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="w-full pl-9 pr-3 py-2 bg-[#1e2f50] border border-[#3b4f6e] rounded-lg text-white text-sm placeholder:text-slate-500 focus:ring-2 focus:ring-blue-500/50 outline-none"
            />
          </div>

          {/* Filter Status */}
          <div>
            <select
              value={filterStatus}
              onChange={(e) => setFilterStatus(e.target.value)}
              className="w-full px-3 py-2 bg-[#1e2f50] border border-[#3b4f6e] rounded-lg text-white text-sm focus:ring-2 focus:ring-blue-500/50 outline-none"
            >
              <option value="all">Semua Status</option>
              <option value="normal">Normal</option>
              <option value="warning">Warning</option>
              <option value="critical">Critical</option>
            </select>
          </div>

          {/* Filter Klasifikasi */}
          <div>
            <select
              value={filterKlasifikasi}
              onChange={(e) => setFilterKlasifikasi(e.target.value)}
              className="w-full px-3 py-2 bg-[#1e2f50] border border-[#3b4f6e] rounded-lg text-white text-sm focus:ring-2 focus:ring-blue-500/50 outline-none"
            >
              <option value="all">Semua Klasifikasi</option>
              {klasifikasiOptions.map((k) => (
                <option key={k} value={k || ''}>{k || 'Unknown'}</option>
              ))}
            </select>
          </div>

          {/* Tombol Reset + Export */}
          <div className="flex gap-2">
            <button
              onClick={resetFilters}
              className="px-4 py-2 bg-slate-600 hover:bg-slate-500 rounded-lg text-xs font-bold text-white flex items-center gap-1 transition"
            >
              <X size={14} /> Reset
            </button>
            <button
              onClick={exportCSV}
              className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 rounded-lg text-xs font-bold text-white flex items-center gap-1 transition"
            >
              <Download size={14} /> Export
            </button>
          </div>
        </div>

        {/* Tanggal Filter */}
        <div className="flex flex-wrap items-center gap-3 mt-3">
          <div className="flex items-center gap-2">
            <Calendar size={14} className="text-slate-400" />
            <span className="text-xs text-slate-400">Dari:</span>
            <input
              type="date"
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
              className="px-2 py-1 bg-[#1e2f50] border border-[#3b4f6e] rounded-lg text-white text-xs focus:ring-2 focus:ring-blue-500/50 outline-none"
            />
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-slate-400">Sampai:</span>
            <input
              type="date"
              value={endDate}
              onChange={(e) => setEndDate(e.target.value)}
              className="px-2 py-1 bg-[#1e2f50] border border-[#3b4f6e] rounded-lg text-white text-xs focus:ring-2 focus:ring-blue-500/50 outline-none"
            />
          </div>
          <span className="text-xs text-slate-500">
            {filteredData.length} data ditampilkan
          </span>
        </div>
      </div>

      {/* Tabel */}
      <div className="overflow-x-auto max-h-[600px] overflow-y-auto border border-[#3b4f6e] rounded-xl">
        <table className="w-full text-left">
          <thead className="sticky top-0 bg-[#1e2f50] z-10">
            <tr className="bg-[#1e2f50] text-white text-[13px] font-black tracking-widest border-b border-[#3b4f6e]">
              <th className="px-4 py-3 text-center">TIME</th>
              <th className="px-4 py-3 text-center">LOSS Km1-4 (dB)</th>
              <th className="px-4 py-3 text-center">TOTAL-L (dB)</th>
              <th className="px-4 py-3 text-center">RETURN Km1-4 (dB)</th>
              <th className="px-4 py-3 text-center">Prx (dBm)</th>
              <th className="px-4 py-3">CLASSIFICATION</th>
              <th className="px-4 py-3 text-center">STATUS</th>
              <th className="px-4 py-3 text-center">SOURCE</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[#3b4f6e]/50">
            {loading ? (
              <tr><td colSpan={8} className="px-4 py-12 text-center text-slate-500">
                <RefreshCw size={18} className="animate-spin mx-auto" />
              </td></tr>
            ) : paginatedData.length === 0 ? (
              <tr><td colSpan={8} className="px-4 py-12 text-center text-slate-500 italic">
                Tidak ada data yang ditemukan
              </td></tr>
            ) : (
              paginatedData.map((row) => (
                <tr key={row.id} className="hover:bg-[#2a3d60]/20 transition-colors">
                  <td className="px-4 py-3 text-center text-white text-xs font-mono">
                    {formatTimestamp(row.timestamp)}
                  </td>
                  <td className="px-4 py-3 text-center text-white text-xs font-mono">
                    {formatLossValue(row.loss_1)} | {formatLossValue(row.loss_2)} |{' '}
                    {formatLossValue(row.loss_3)} | {formatLossValue(row.loss_4)}
                  </td>
                  <td className="px-4 py-3 text-center text-emerald-400 font-bold text-xs font-mono">
                    {formatLossValue(row.total_l_4)}
                  </td>
                  <td className="px-4 py-3 text-center text-white text-xs font-mono">
                    {formatReturnValue(row.return_1)} | {formatReturnValue(row.return_2)} |{' '}
                    {formatReturnValue(row.return_3)} | {formatReturnValue(row.return_4)}
                  </td>
                  <td className="px-4 py-3 text-center text-blue-400 font-bold text-xs font-mono">
                    {row.prx != null ? `${row.prx.toFixed(1)} dBm` : '—'}
                  </td>
                  <td className="px-4 py-3">
                    <span className={`px-3 py-1 rounded-full text-[11px] font-black border ${
                      row.klasifikasi === 'Normal' ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30' :
                      row.klasifikasi === 'Warning' ? 'bg-amber-500/20 text-amber-400 border-amber-500/30' :
                      row.klasifikasi === 'Fiber Cut' ? 'bg-red-500/20 text-red-400 border-red-500/30' :
                      'bg-amber-500/20 text-amber-400 border-amber-500/30'
                    }`}>
                      {row.klasifikasi === 'hampir putus' ? 'Nearly Cut' : (row.klasifikasi || 'Unknown')}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-center"><StatusBadge status={row.status} /></td>
                  <td className="px-4 py-3 text-center text-xs text-slate-400">
                    {row.source === 'ocr' ? '📷 OCR' : row.source === 'manual' ? '✏️ Manual' : row.source === 'sheets' ? '📊 Sheets' : '—'}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {!loading && filteredData.length > 0 && (
        <div className="flex justify-between items-center mt-4">
          <div className="text-xs text-slate-400">
            Menampilkan {startIndex + 1} - {Math.min(startIndex + itemsPerPage, filteredData.length)} dari {filteredData.length} data
          </div>
          <div className="flex items-center gap-2">
            <select
              value={itemsPerPage}
              onChange={(e) => setItemsPerPage(Number(e.target.value))}
              className="px-2 py-1 bg-[#1e2f50] border border-[#3b4f6e] rounded-lg text-white text-xs focus:ring-2 focus:ring-blue-500/50 outline-none"
            >
              <option value={25}>25</option>
              <option value={50}>50</option>
              <option value={100}>100</option>
              <option value={200}>200</option>
            </select>
            <button
              onClick={() => setCurrentPage(prev => Math.max(1, prev - 1))}
              disabled={currentPage === 1}
              className="px-3 py-1 bg-[#1e2f50] border border-[#3b4f6e] rounded-lg text-white text-xs disabled:opacity-50 hover:bg-[#2a3d60] transition"
            >
              <ChevronLeft size={14} />
            </button>
            <span className="text-xs text-slate-400">
              {currentPage} / {totalPages}
            </span>
            <button
              onClick={() => setCurrentPage(prev => Math.min(totalPages, prev + 1))}
              disabled={currentPage === totalPages}
              className="px-3 py-1 bg-[#1e2f50] border border-[#3b4f6e] rounded-lg text-white text-xs disabled:opacity-50 hover:bg-[#2a3d60] transition"
            >
              <ChevronRight size={14} />
            </button>
          </div>
        </div>
      )}

    </div>
  );
};

export default History;