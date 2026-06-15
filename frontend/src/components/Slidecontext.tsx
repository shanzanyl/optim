// frontend/src/contexts/SlideContext.tsx

import React, { createContext, useContext, useState, useEffect, ReactNode } from 'react';

interface SlideContextType {
  currentIndex: number;
  totalData: number;
  setCurrentIndex: (index: number) => void;
  setTotalData: (total: number) => void;
  nextSlide: () => void;
  prevSlide: () => void;
  autoPlay: boolean;
  setAutoPlay: (auto: boolean) => void;
  resetSlideState: () => void;
}

const SlideContext = createContext<SlideContextType | undefined>(undefined);

export const useSlide = () => {
  const context = useContext(SlideContext);
  if (!context) {
    throw new Error('useSlide must be used within SlideProvider');
  }
  return context;
};

interface SlideProviderProps {
  children: ReactNode;
}

export const SlideProvider: React.FC<SlideProviderProps> = ({ children }) => {
  // Load dari localStorage
  const [currentIndex, setCurrentIndex] = useState(() => {
    const saved = localStorage.getItem('slide_current_index');
    const parsed = saved ? parseInt(saved, 10) : 0;
    return isNaN(parsed) ? 0 : parsed;
  });
  
  const [totalData, setTotalData] = useState(0);
  
  const [autoPlay, setAutoPlay] = useState(() => {
    const saved = localStorage.getItem('slide_auto_play');
    return saved ? saved === 'true' : true;
  });

  const resetSlideState = () => {
    localStorage.removeItem('slide_current_index');
    localStorage.removeItem('slide_auto_play');
    setCurrentIndex(0);
    setTotalData(0);
    setAutoPlay(true);
  };

  // 🔥 PERBAIKAN: Ketika totalData berubah, jangan reset currentIndex jika masih valid
  useEffect(() => {
    if (totalData > 0 && currentIndex >= totalData) {
      // Jika currentIndex melebihi totalData, pindah ke data terakhir
      setCurrentIndex(totalData - 1);
    }
  }, [totalData, currentIndex]);

  // Simpan currentIndex ke localStorage
  useEffect(() => {
    localStorage.setItem('slide_current_index', currentIndex.toString());
  }, [currentIndex]);

  // Simpan autoPlay ke localStorage
  useEffect(() => {
    localStorage.setItem('slide_auto_play', autoPlay.toString());
  }, [autoPlay]);

  const nextSlide = () => {
    if (totalData > 0) {
      setCurrentIndex((prev) => (prev + 1) % totalData);
    }
  };

  const prevSlide = () => {
    if (totalData > 0) {
      setCurrentIndex((prev) => (prev - 1 + totalData) % totalData);
    }
  };

  return (
    <SlideContext.Provider
      value={{
        currentIndex,
        totalData,
        setCurrentIndex,
        setTotalData,
        nextSlide,
        prevSlide,
        autoPlay,
        setAutoPlay,
        resetSlideState,
      }}
    >
      {children}
    </SlideContext.Provider>
  );
};