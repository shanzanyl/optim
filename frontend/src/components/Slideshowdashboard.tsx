// frontend/src/components/Slideshowdashboard.tsx
import React, { useState, useEffect } from 'react';
import { useSlide } from '../Context/SlideContext';
import { API_BASE } from '../services/api';

const SlideShow: React.FC = () => {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  
  const { 
    currentIndex, 
    setCurrentIndex, 
    totalData, 
    setTotalData,
    autoPlay, 
    setAutoPlay 
  } = useSlide();

  const fetchData = async (index: number) => {
    setLoading(true);
    try {
      const token = localStorage.getItem('token');
      const response = await fetch(`${API_BASE}/api/slide/${index}`, {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      const result = await response.json();
      setData(result);
      setTotalData(result.total_data || 0);
      // API returns 1-based index, convert to 0-based
      const apiIndex = (result.current_index || 1) - 1;
      if (apiIndex !== currentIndex) {
        setCurrentIndex(apiIndex);
      }
    } catch (error) {
      console.error('Error:', error);
    } finally {
      setLoading(false);
    }
  };

  // Fetch data when currentIndex changes
  useEffect(() => {
    if (totalData > 0 && currentIndex < totalData) {
      fetchData(currentIndex);
    }
  }, [currentIndex, totalData]);

  // Auto-slide
  useEffect(() => {
    if (!autoPlay || totalData === 0) return;
    const interval = setInterval(() => {
      setCurrentIndex((prev) => (prev + 1) % totalData);
    }, 30000);
    return () => clearInterval(interval);
  }, [autoPlay, totalData, setCurrentIndex]);

  const handlePrev = () => {
    setCurrentIndex((prev) => (prev - 1 + totalData) % totalData);
  };

  const handleNext = () => {
    setCurrentIndex((prev) => (prev + 1) % totalData);
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-[#0b1120] flex items-center justify-center">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600"></div>
      </div>
    );
  }

  const progressPercent = totalData > 0 ? ((currentIndex + 1) / totalData) * 100 : 0;

  return (
    <div className="min-h-screen bg-[#0b1120] text-white p-8">
      <div className="max-w-4xl mx-auto">
        
        {/* Header */}
        <div className="text-center mb-8">
          <h1 className="text-3xl font-bold">OptiM CAPSTONE DESIGN</h1>
          <div className="mt-4">
            <div className="flex justify-between text-sm mb-2">
              <span>Progress: {currentIndex + 1} / {totalData}</span>
              <span>{Math.round(progressPercent)}%</span>
            </div>
            <div className="w-full bg-slate-700 rounded-full h-2">
              <div className="bg-emerald-500 h-2 rounded-full transition-all duration-500" style={{ width: `${progressPercent}%` }} />
            </div>
          </div>
          <button
            onClick={() => setAutoPlay(!autoPlay)}
            className="mt-4 text-sm bg-slate-800 px-4 py-2 rounded-lg"
          >
            {autoPlay ? '⏸ Pause' : '▶ Play'}
          </button>
        </div>

        {/* Loss Cards */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-6 mb-8">
          {[1, 2, 3, 4].map(km => (
            <div key={km} className="bg-[#0f172a] rounded-2xl p-6 text-center border border-slate-800">
              <p className="text-xs text-slate-500">Loss KM {km}</p>
              <p className="text-2xl font-bold text-white">{data?.[`loss_${km}`]?.toFixed(2) || 0} dB</p>
            </div>
          ))}
        </div>

        {/* Return Cards */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-6 mb-8">
          {[1, 2, 3, 4].map(km => (
            <div key={km} className="bg-[#0f172a] rounded-2xl p-6 text-center border border-slate-800">
              <p className="text-xs text-slate-500">Return KM {km}</p>
              <p className="text-2xl font-bold text-white">{data?.[`return_${km}`]?.toFixed(1) || 0} dB</p>
            </div>
          ))}
        </div>

        {/* Classification Result */}
        <div className="bg-[#0f172a] rounded-2xl p-8 text-center border border-slate-800">
          <p className="text-xs text-slate-500 mb-2">Klasifikasi Gangguan</p>
          <p className={`text-3xl font-bold ${
            data?.klasifikasi === 'Normal' ? 'text-emerald-400' : 'text-amber-400'
          }`}>
            {data?.klasifikasi || 'Unknown'}
          </p>
          <p className="text-sm text-slate-500 mt-2">Confidence: {data?.confidence?.toFixed(1)}%</p>
          <p className="text-xs mt-4 text-slate-600">
            {autoPlay ? `Berikutnya dalam 30 detik...` : 'Paused - klik Play untuk lanjut'}
          </p>
        </div>

        {/* Signal Power */}
        {data?.prx && (
          <div className="mt-8 bg-[#0f172a] rounded-2xl p-6 text-center border border-slate-800">
            <p className="text-xs text-slate-500">Signal Power (Prx)</p>
            <p className="text-2xl font-bold text-blue-400">{data.prx} dBm</p>
          </div>
        )}

        {/* Timestamp */}
        {data?.timestamp && (
          <p className="text-center text-xs text-slate-600 mt-8">
            Data dari: {new Date(data.timestamp).toLocaleString()}
          </p>
        )}

        {/* Manual Navigation Buttons */}
        <div className="flex justify-center gap-4 mt-8">
          <button
            onClick={handlePrev}
            className="px-4 py-2 bg-slate-800 rounded-lg text-sm"
          >
            ◀ Sebelumnya
          </button>
          <button
            onClick={handleNext}
            className="px-4 py-2 bg-blue-600 rounded-lg text-sm"
          >
            Selanjutnya ▶
          </button>
        </div>

      </div>
    </div>
  );
};

export default SlideShow;