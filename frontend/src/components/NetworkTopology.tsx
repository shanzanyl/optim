// frontend/src/components/NetworkTopology.tsx
import React, { useEffect, useState } from 'react';
import { Network } from 'lucide-react';

interface NetworkTopologyProps {
  losses: number[];
  prx: number;
  klasifikasi?: string;
  status?: string;
  cutKM?: number;
  currentRecord?: any;
}

const NetworkTopology: React.FC<NetworkTopologyProps> = ({
  losses,
  prx,
  klasifikasi = 'Normal',
  status = '',
  cutKM = 1,
  currentRecord,
}) => {
  const [blinkStates, setBlinkStates] = useState<boolean[]>([false, false, false, false]);
  const [isMobile, setIsMobile] = useState(false);
  const LOSS_THRESHOLD = 1.2;
  
  // Deteksi layar mobile
  useEffect(() => {
    const checkMobile = () => {
      setIsMobile(window.innerWidth < 768);
    };
    checkMobile();
    window.addEventListener('resize', checkMobile);
    return () => window.removeEventListener('resize', checkMobile);
  }, []);
  
  const normalizedKlasifikasi = (klasifikasi || 'Normal').toLowerCase();
  const isGangguan = normalizedKlasifikasi !== 'normal';
  const isFiberCut = normalizedKlasifikasi === 'fiber cut';
  
  const getHighLossKM = (): number[] => {
    const highLossKM: number[] = [];
    losses.forEach((loss, index) => {
      const km = index + 1;
      const isHighLoss = loss > LOSS_THRESHOLD;
      const isCut = isFiberCut && loss === 0;
      
      if (isHighLoss || isCut) {
        highLossKM.push(km);
      }
    });
    return highLossKM;
  };
  
  const highLossKMList = getHighLossKM();
  const isAnyHighLoss = highLossKMList.length > 0;
  
  useEffect(() => {
    const intervals: ReturnType<typeof setInterval>[] = [];
    
    setBlinkStates([false, false, false, false]);
    
    if (isAnyHighLoss) {
      highLossKMList.forEach((km) => {
        const index = km - 1;
        const interval = setInterval(() => {
          setBlinkStates(prev => {
            const newState = [...prev];
            newState[index] = !newState[index];
            return newState;
          });
        }, 500);
        intervals.push(interval);
      });
    }
    
    return () => {
      intervals.forEach(interval => clearInterval(interval));
    };
  }, [isAnyHighLoss, losses.join(',')]);
  
  const getGangguanColor = (km: number) => {
    const loss = losses[km - 1];
    const isHighLoss = loss > LOSS_THRESHOLD;
    const isCut = isFiberCut && loss === 0;
    
    if (isCut) return '#ef4444';
    if (isHighLoss) {
      switch(normalizedKlasifikasi) {
        case 'bending': return '#f59e0b';
        case 'bad splice': return '#f59e0b';
        case 'air gap': return '#f59e0b';
        case 'dirty connector': return '#f59e0b';
        case 'hampir putus': return '#e84910';
        default: return '#f59e0b';
      }
    }
    return '#10b981';
  };
  
  const getNodeStyle = (km: number) => {
    const loss = losses[km - 1];
    const isHighLoss = loss > LOSS_THRESHOLD;
    const isCut = isFiberCut && loss === 0;
    const isBlinking = blinkStates[km - 1];
    const gangguanColor = getGangguanColor(km);
    
    if (!isHighLoss && !isCut) {
      return {
        backgroundColor: '#10b981',
        borderColor: '#10b981',
        textColor: 'text-emerald-400',
        boxShadow: 'none'
      };
    }
    
    return {
      backgroundColor: isBlinking ? gangguanColor : `${gangguanColor}80`,
      borderColor: gangguanColor,
      textColor: isCut ? 'text-red-400' : 'text-amber-400',
      boxShadow: isBlinking ? `0 0 20px ${gangguanColor}` : 'none'
    };
  };
  
  const validConfidence = currentRecord?.confidence 
    ? Math.min(currentRecord.confidence, 1.0) 
    : null;
  const confidencePercent = validConfidence 
    ? `${(validConfidence * 100).toFixed(1)}%` 
    : 'N/A';
  
  const getGangguanMessage = () => {
    if (!isAnyHighLoss && !isFiberCut) return null;
    
    if (isFiberCut) {
      return `FIBER CUT terdeteksi di KM ${cutKM}! Kabel serat optik putus total.`;
    }
    
    const highLossKM = highLossKMList.join(', ');
    switch(normalizedKlasifikasi) {
      case 'bending':
        return `BENDING terdeteksi di KM ${highLossKM}!`;
      case 'bad splice':
        return `BAD SPLICE terdeteksi di KM ${highLossKM}!`;
      case 'air gap':
        return `AIR GAP terdeteksi di KM ${highLossKM}! `;
      case 'dirty connector':
        return `DIRTY CONNECTOR terdeteksi di KM ${highLossKM}!`;
      case 'hampir putus':
        return `HAMPIR PUTUS terdeteksi di KM ${highLossKM}!`;
      default:
        return `${klasifikasi} terdeteksi di KM ${highLossKM}!`;
    }
  };
  
  const gangguanMessage = getGangguanMessage();

  // Data node untuk mapping
  const nodes = [
    { id: 'ols', label: 'OLS', type: 'source', color: 'blue', width: isMobile ? 'w-10' : 'w-14', height: isMobile ? 'h-10' : 'h-14', textSize: isMobile ? 'text-[10px]' : 'text-sm' },
    { id: 'km1', label: 'KM 1', type: 'km', km: 1, width: isMobile ? 'w-10' : 'w-14', height: isMobile ? 'h-10' : 'h-14', textSize: isMobile ? 'text-[10px]' : 'text-sm' },
    { id: 'km2', label: 'KM 2', type: 'km', km: 2, width: isMobile ? 'w-10' : 'w-14', height: isMobile ? 'h-10' : 'h-14', textSize: isMobile ? 'text-[10px]' : 'text-sm' },
    { id: 'km3', label: 'KM 3', type: 'km', km: 3, width: isMobile ? 'w-10' : 'w-14', height: isMobile ? 'h-10' : 'h-14', textSize: isMobile ? 'text-[10px]' : 'text-sm' },
    { id: 'km4', label: 'KM 4', type: 'km', km: 4, width: isMobile ? 'w-10' : 'w-14', height: isMobile ? 'h-10' : 'h-14', textSize: isMobile ? 'text-[10px]' : 'text-sm' },
    { id: 'opm', label: 'OPM', type: 'destination', color: 'emerald', width: isMobile ? 'w-10' : 'w-14', height: isMobile ? 'h-10' : 'h-14', textSize: isMobile ? 'text-[10px]' : 'text-sm' },
  ];

  return (
    <div className="bg-[#1e2f50] border border-[#3b4f6e] rounded-xl md:rounded-2xl p-3 md:p-6 shadow-xl">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3 mb-4 md:mb-8">
        <div className="flex items-center gap-2 md:gap-3">
          <div className="p-1.5 md:p-2 bg-blue-500/10 rounded-lg">
          </div>
          <div>
          </div>
        </div>
        
        <div className={`px-2 md:px-3 py-1 rounded-full text-[8px] md:text-xs font-bold ${
          !isAnyHighLoss && !isFiberCut
            ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'
            : 'bg-red-500/20 text-red-400 border border-red-500/30'
        }`}>
          {isMobile ? `${klasifikasi}` : `Klasifikasi: ${klasifikasi} ${status ? `(${status})` : ''} `}
        </div>
      </div>
      
      {/* Topology Diagram - Responsive Layout */}
      {isMobile ? (
        // ========== MOBILE VERSION: Vertical / Stacked Layout ==========
        <div className="flex flex-col items-center gap-2 py-4">
          {/* OLS */}
          <div className="text-center">
            <div className="w-12 h-12 mx-auto bg-gradient-to-br from-blue-600 to-blue-800 rounded-full flex items-center justify-center shadow-lg border border-blue-400/30">
              <span className="text-white font-bold text-xs">OLS</span>
            </div>
            <p className="text-[9px] font-bold text-blue-400 mt-1">OLS</p>
            <p className="text-[7px] text-slate-400">Light Source</p>
          </div>
          
          {/* Vertical line */}
          <div className="w-0.5 h-4 bg-[#3b4f6e]"></div>
          
          {/* KM nodes in vertical layout */}
          {[1, 2, 3, 4].map((km) => {
            const lossValue = losses[km - 1] || 0;
            const isCut = isFiberCut && km >= cutKM;
            const nodeStyle = getNodeStyle(km);
            
            if (isCut) {
              return (
                <div key={km} className="text-center w-full">
                  <div className="w-12 h-12 mx-auto rounded-full flex items-center justify-center shadow-lg transition-all duration-150 border-2"
                    style={{
                      backgroundColor: nodeStyle.backgroundColor,
                      boxShadow: nodeStyle.boxShadow,
                      borderColor: nodeStyle.borderColor
                    }}>
                    <span className="text-white font-bold text-xs">KM {km}</span>
                  </div>
                  <p className={`text-[9px] font-bold mt-0.5 ${nodeStyle.textColor}`}>
                    Loss: {lossValue?.toFixed(2)} dB
                  </p>
                  <p className="text-[8px] font-bold text-red-400 mt-0.5 animate-pulse">FIBER CUT</p>
                  <div className="w-0.5 h-4 bg-red-500/50 mx-auto mt-1"></div>
                </div>
              );
            }
            
            return (
              <div key={km} className="text-center w-full">
                <div className="w-12 h-12 mx-auto rounded-full flex items-center justify-center shadow-lg transition-all duration-150 border-2"
                  style={{
                    backgroundColor: nodeStyle.backgroundColor,
                    boxShadow: nodeStyle.boxShadow,
                    borderColor: nodeStyle.borderColor
                  }}>
                  <span className="text-white font-bold text-xs">KM {km}</span>
                </div>
                <p className={`text-[9px] font-bold mt-0.5 ${nodeStyle.textColor}`}>
                  Loss: {lossValue === 0 ? '---' : `${lossValue?.toFixed(2)} dB`}
                </p>
                {lossValue > LOSS_THRESHOLD && (
                  <p className="text-[7px] font-bold text-amber-400 mt-0.5 animate-pulse">HIGH LOSS</p>
                )}
                <div className="w-0.5 h-4 bg-[#3b4f6e] mx-auto mt-1"></div>
              </div>
            );
          })}
          
          {/* OPM */}
          <div className="text-center">
            <div className="w-12 h-12 mx-auto bg-gradient-to-br from-emerald-600 to-emerald-800 rounded-full flex items-center justify-center shadow-lg border border-emerald-400/30">
              <span className="text-white font-bold text-xs">OPM</span>
            </div>
            <p className="text-[9px] font-bold text-emerald-400 mt-1">OPM</p>
            <p className="text-[7px] text-slate-400">Power Meter</p>
            <p className="text-[8px] font-mono text-emerald-400 mt-0.5">{prx?.toFixed(1)} dBm</p>
          </div>
        </div>
      ) : (
        // ========== DESKTOP VERSION: Horizontal Layout ==========
        <div className="flex items-center justify-between">
          {/* OLS */}
          <div className="text-center flex-shrink-0 w-20">
            <div className="w-14 h-14 mx-auto bg-gradient-to-br from-blue-600 to-blue-800 rounded-full flex items-center justify-center shadow-lg border border-blue-400/30">
              <span className="text-white font-bold text-sm">OLS</span>
            </div>
            <p className="text-[10px] font-bold text-blue-400 mt-2">OLS</p>
            <p className="text-[8px] text-slate-400">Light Source</p>
          </div>
          
          <div className="flex-1 h-0.5 bg-[#3b4f6e] mx-2"></div>
          
          {/* KM1 */}
          <div className="text-center flex-shrink-0 w-20">
            <div className="w-14 h-14 mx-auto rounded-full flex items-center justify-center shadow-lg transition-all duration-150 border-2"
              style={{
                backgroundColor: getNodeStyle(1).backgroundColor,
                boxShadow: getNodeStyle(1).boxShadow,
                borderColor: getNodeStyle(1).borderColor
              }}>
              <span className="text-white font-bold text-sm">KM 1</span>
            </div>
            <p className={`text-[10px] font-bold mt-1 ${getNodeStyle(1).textColor}`}>
              Loss: {losses[0]?.toFixed(2)} dB
            </p>
            {losses[0] > LOSS_THRESHOLD && (
              <p className="text-[8px] font-bold text-amber-400 mt-0.5 animate-pulse">HIGH LOSS</p>
            )}
          </div>
          
          <div className="flex-1 h-0.5 bg-[#3b4f6e] mx-2"></div>
          
          {/* KM2 */}
          <div className="text-center flex-shrink-0 w-20">
            <div className="w-14 h-14 mx-auto rounded-full flex items-center justify-center shadow-lg transition-all duration-150 border-2"
              style={{
                backgroundColor: getNodeStyle(2).backgroundColor,
                boxShadow: getNodeStyle(2).boxShadow,
                borderColor: getNodeStyle(2).borderColor
              }}>
              <span className="text-white font-bold text-sm">KM 2</span>
            </div>
            <p className={`text-[10px] font-bold mt-1 ${getNodeStyle(2).textColor}`}>
              Loss: {losses[1]?.toFixed(2)} dB
            </p>
            {losses[1] > LOSS_THRESHOLD && (
              <p className="text-[8px] font-bold text-amber-400 mt-0.5 animate-pulse">HIGH LOSS</p>
            )}
          </div>
          
          <div className="flex-1 h-0.5 bg-[#3b4f6e] mx-2"></div>
          
          {/* KM3 */}
          <div className="text-center flex-shrink-0 w-20">
            <div className="w-14 h-14 mx-auto rounded-full flex items-center justify-center shadow-lg transition-all duration-150 border-2"
              style={{
                backgroundColor: getNodeStyle(3).backgroundColor,
                boxShadow: getNodeStyle(3).boxShadow,
                borderColor: getNodeStyle(3).borderColor
              }}>
              <span className="text-white font-bold text-sm">KM 3</span>
            </div>
            <p className={`text-[10px] font-bold mt-1 ${getNodeStyle(3).textColor}`}>
              Loss: {losses[2]?.toFixed(2)} dB
            </p>
            {losses[2] > LOSS_THRESHOLD && (
              <p className="text-[8px] font-bold text-amber-400 mt-0.5 animate-pulse">HIGH LOSS</p>
            )}
          </div>
          
          <div className="flex-1 h-0.5 bg-[#3b4f6e] mx-2"></div>
          
          {/* KM4 */}
          <div className="text-center flex-shrink-0 w-20">
            <div className="w-14 h-14 mx-auto rounded-full flex items-center justify-center shadow-lg transition-all duration-150 border-2"
              style={{
                backgroundColor: getNodeStyle(4).backgroundColor,
                boxShadow: getNodeStyle(4).boxShadow,
                borderColor: getNodeStyle(4).borderColor
              }}>
              <span className="text-white font-bold text-sm">KM 4</span>
            </div>
            <p className={`text-[10px] font-bold mt-1 ${getNodeStyle(4).textColor}`}>
              Loss: {losses[3] === 0 || losses[3] === null || losses[3] === undefined ? '---' : `${losses[3]?.toFixed(2)} dB`}
            </p>
            {losses[3] > LOSS_THRESHOLD && (
              <p className="text-[8px] font-bold text-amber-400 mt-0.5 animate-pulse">HIGH LOSS</p>
            )}
          </div>
          
          <div className="flex-1 h-0.5 bg-[#3b4f6e] mx-2"></div>
          
          {/* OPM */}
          <div className="text-center flex-shrink-0 w-20">
            <div className="w-14 h-14 mx-auto bg-gradient-to-br from-emerald-600 to-emerald-800 rounded-full flex items-center justify-center shadow-lg border border-emerald-400/30">
              <span className="text-white font-bold text-sm">OPM</span>
            </div>
            <p className="text-[10px] font-bold text-emerald-400 mt-2">OPM</p>
            <p className="text-[8px] text-slate-400">Power Meter</p>
            <p className="text-[9px] font-mono text-emerald-400 mt-0.5">{prx?.toFixed(1)} dBm</p>
          </div>
        </div>
      )}
      
      {/* Alert Gangguan - Responsif */}
      {gangguanMessage && (
        <div className={`mt-4 md:mt-6 p-2 md:p-3 rounded-lg border ${
          isFiberCut 
            ? 'bg-red-500/10 border-red-500/20'
            : 'bg-amber-500/10 border-amber-500/20'
        }`}>
          <p className={`text-[9px] md:text-[11px] text-center font-bold ${
            isFiberCut ? 'text-red-400' : 'text-amber-400'
          }`}>
            {gangguanMessage}
          </p>
        </div>
      )}
    </div>
  );
};

export default NetworkTopology;