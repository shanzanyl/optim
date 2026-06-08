// src/Context/SlideContext.tsx
import React, { createContext, useContext, useState, useEffect, ReactNode } from 'react';

interface SlideContextType {
  currentIndex: number;
  totalData: number;
  setCurrentIndex: (index: number | ((prev: number) => number)) => void;
  setTotalData: (total: number) => void;
  nextSlide: () => void;
  prevSlide: () => void;
  autoPlay: boolean;
  setAutoPlay: (auto: boolean) => void;
  resetToLatest: () => void;
}

const SlideContext = createContext<SlideContextType | undefined>(undefined);

// ✅ PASTIKAN INI ADA - export function useSlide
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

// ✅ PASTIKAN INI ADA - export function SlideProvider
export const SlideProvider: React.FC<SlideProviderProps> = ({ children }) => {
  const [currentIndex, setCurrentIndex] = useState<number>(() => {
    const saved = localStorage.getItem('slide_current_index');
    return saved ? parseInt(saved, 10) : 0;
  });
  
  const [totalData, setTotalData] = useState<number>(() => {
    const saved = localStorage.getItem('slide_total_data');
    return saved ? parseInt(saved, 10) : 0;
  });
  
  const [autoPlay, setAutoPlay] = useState<boolean>(() => {
    const saved = localStorage.getItem('slide_auto_play');
    return saved ? saved === 'true' : true;
  });

  useEffect(() => {
    localStorage.setItem('slide_current_index', currentIndex.toString());
  }, [currentIndex]);

  useEffect(() => {
    localStorage.setItem('slide_total_data', totalData.toString());
  }, [totalData]);

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

  const resetToLatest = () => {
    if (totalData > 0) {
      setCurrentIndex(totalData - 1);
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
        resetToLatest,
      }}
    >
      {children}
    </SlideContext.Provider>
  );
};