import React, { useState, useRef, useEffect } from 'react';
import { MessageCircle, X, Send, Minimize2 } from 'lucide-react';

interface ChatMessage {
  id: string;
  text: string;
  sender: 'user' | 'bot';
  timestamp: Date;
}

// 🔥 AMBIL API BASE DARI ENVIRONMENT VARIABLE
const API_BASE = ((import.meta as any).env?.VITE_API_URL as string | undefined)?.replace('/api', '') || 'http://localhost:8000';

const Chatbot: React.FC = () => {
  const [isOpen, setIsOpen] = useState(false);
  const [isMinimized, setIsMinimized] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: '1',
      text: 'Halo! Saya asisten AI OptiM. Saya bisa membantu Anda menganalisis data monitoring fiber optik.<br/><br/>📊 <strong>Saya punya akses ke:</strong><br/>• Data OTDR (Loss, Return, PRX)<br/>• Klasifikasi gangguan (Normal, Bending, Fiber Cut, dll)<br/>• Statistik dashboard<br/>• History pengukuran<br/><br/>Apa yang ingin Anda tanyakan?',
      sender: 'bot',
      timestamp: new Date()
    }
  ]);
  const [inputValue, setInputValue] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  useEffect(() => {
    if (isOpen && !isMinimized) {
      inputRef.current?.focus();
    }
  }, [isOpen, isMinimized]);

  // 🔥 Ambil data dashboard summary untuk dikirim ke chatbot
  const fetchDashboardSummary = async () => {
    try {
      const token = localStorage.getItem('token');
      const response = await fetch(`${API_BASE}/api/dashboard?limit=100`, {
        headers: { 'Authorization': `Bearer ${token}` },
      });
      const dashboard = await response.json();
      
      if (dashboard?.data?.length > 0) {
        const data = dashboard.data;
        const totalData = data.length;
        const normalCount = data.filter((d: any) => d.klasifikasi === 'Normal').length;
        const gangguanCount = totalData - normalCount;
        
        // Hitung rata-rata loss per KM
        const avgLoss = [0, 0, 0, 0];
        data.forEach((d: any) => {
          avgLoss[0] += d.loss_1 || 0;
          avgLoss[1] += d.loss_2 || 0;
          avgLoss[2] += d.loss_3 || 0;
          avgLoss[3] += d.loss_4 || 0;
        });
        
        // Hitung distribusi gangguan
        const faultMap: Record<string, number> = {};
        data.forEach((d: any) => {
          const k = d.klasifikasi || 'Unknown';
          faultMap[k] = (faultMap[k] || 0) + 1;
        });
        const topGangguan = Object.entries(faultMap)
          .map(([name, count]) => ({ name, count: count as number }))
          .sort((a, b) => b.count - a.count)
          .slice(0, 5);
        
        const latestData = data[data.length - 1];
        
        return {
          totalData,
          normalCount,
          gangguanCount,
          latestKlasifikasi: latestData?.klasifikasi || 'Unknown',
          latestStatus: latestData?.status || 'Unknown',
          latestLoss1: latestData?.loss_1 || 0,
          latestLoss2: latestData?.loss_2 || 0,
          latestLoss3: latestData?.loss_3 || 0,
          latestLoss4: latestData?.loss_4 || 0,
          latestPrx: latestData?.prx || 0,
          averageLoss: avgLoss.map(l => (l / totalData).toFixed(2)),
          topGangguan,
        };
      }
      return null;
    } catch (error) {
      console.error('Failed to fetch dashboard summary:', error);
      return null;
    }
  };

  const sendMessage = async () => {
    if (!inputValue.trim()) return;

    const userMessage: ChatMessage = {
      id: Date.now().toString(),
      text: inputValue,
      sender: 'user',
      timestamp: new Date()
    };
    setMessages(prev => [...prev, userMessage]);
    setInputValue('');
    setIsLoading(true);

    try {
      const token = localStorage.getItem('token');
      
      // 🔥 Ambil data summary real-time dari dashboard
      const dashboardSummary = await fetchDashboardSummary();
      
      // 🔥 Context yang kaya untuk AI
      const contextState = {
        "Halaman": window.location.pathname,
        "Total Data": dashboardSummary?.totalData || 'Unknown',
        "Data Normal": dashboardSummary?.normalCount || 0,
        "Data Gangguan": dashboardSummary?.gangguanCount || 0,
        "Klasifikasi Terakhir": dashboardSummary?.latestKlasifikasi || 'Unknown',
        "Status Terakhir": dashboardSummary?.latestStatus || 'Unknown',
        "Loss Terakhir": `KM1=${dashboardSummary?.latestLoss1} dB, KM2=${dashboardSummary?.latestLoss2} dB, KM3=${dashboardSummary?.latestLoss3} dB, KM4=${dashboardSummary?.latestLoss4} dB`,
        "Prx Terakhir": `${dashboardSummary?.latestPrx} dBm`,
        "Rata-rata Loss KM1-4 (dB)": dashboardSummary?.averageLoss?.join(', ') || 'N/A',
        "Top 5 Jenis Gangguan": dashboardSummary?.topGangguan?.map(g => `${g.name}: ${g.count}`).join(', ') || 'Tidak ada',
      };

      const response = await fetch(`${API_BASE}/api/chat`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({
          message: userMessage.text,
          context_state: contextState
        })
      });

      const data = await response.json();
      
      const botMessage: ChatMessage = {
        id: (Date.now() + 1).toString(),
        text: data.response || 'Maaf, saya tidak bisa menjawab saat ini.',
        sender: 'bot',
        timestamp: new Date()
      };
      setMessages(prev => [...prev, botMessage]);
      
    } catch (error) {
      console.error('Chat error:', error);
      const errorMessage: ChatMessage = {
        id: (Date.now() + 1).toString(),
        text: 'Maaf, terjadi kesalahan. Pastikan server backend berjalan.',
        sender: 'bot',
        timestamp: new Date()
      };
      setMessages(prev => [...prev, errorMessage]);
    } finally {
      setIsLoading(false);
    }
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  const toggleChat = () => {
    setIsOpen(!isOpen);
    setIsMinimized(false);
  };

  const toggleMinimize = () => {
    setIsMinimized(!isMinimized);
  };

  // Tombol floating - responsif
  if (!isOpen) {
    return (
      <button
        onClick={toggleChat}
        className="fixed bottom-4 right-4 sm:bottom-6 sm:right-6 w-10 h-10 sm:w-12 sm:h-12 md:w-14 md:h-14 
          bg-gradient-to-r from-blue-600 to-cyan-600 rounded-full flex items-center justify-center 
          shadow-xl shadow-blue-600/30 hover:scale-105 transition-all duration-300 z-50 group"
      >
        <MessageCircle className="w-5 h-5 sm:w-6 sm:h-6 text-white group-hover:rotate-12 transition-transform" />
        <span className="absolute -top-1 -right-1 w-2.5 h-2.5 sm:w-3 sm:h-3 bg-red-500 rounded-full animate-pulse"></span>
      </button>
    );
  }

  // Chat window - responsif (lebar penuh di mobile)
  return (
    <div className={`fixed bottom-0 right-0 sm:bottom-4 sm:right-4 z-50 transition-all duration-300 
      ${isMinimized ? 'w-full sm:w-80 h-14' : 'w-full sm:w-96 h-[80vh] sm:h-[500px]'}`}>
      <div className="bg-slate-800/95 backdrop-blur-xl border border-slate-700 rounded-t-2xl sm:rounded-2xl 
        shadow-2xl flex flex-col h-full overflow-hidden">
        
        {/* Header */}
        <div className="flex items-center justify-between p-3 sm:p-4 border-b border-slate-700 bg-gradient-to-r from-blue-600/20 to-cyan-600/20">
          <div className="flex items-center gap-2 sm:gap-3">
            <div className="w-7 h-7 sm:w-8 sm:h-8 bg-gradient-to-br from-blue-500 to-cyan-500 rounded-lg flex items-center justify-center">
              <span className="text-white text-xs sm:text-sm font-bold">AI</span>
            </div>
            <div>
              <h3 className="text-xs sm:text-sm font-bold text-white">OptiM AI Assistant</h3>
              <p className="text-[8px] sm:text-[10px] text-slate-400">Powered by Gemini AI • Data Real</p>
            </div>
          </div>
          <div className="flex items-center gap-1 sm:gap-2">
            <button
              onClick={toggleMinimize}
              className="p-1.5 sm:p-2 text-slate-400 hover:text-white transition-colors rounded-lg hover:bg-slate-700"
            >
              <Minimize2 className="w-3.5 h-3.5 sm:w-4 sm:h-4" />
            </button>
            <button
              onClick={toggleChat}
              className="p-1.5 sm:p-2 text-slate-400 hover:text-red-400 transition-colors rounded-lg hover:bg-slate-700"
            >
              <X className="w-3.5 h-3.5 sm:w-4 sm:h-4" />
            </button>
          </div>
        </div>

        {!isMinimized && (
          <>
            {/* Messages Container */}
            <div className="flex-1 overflow-y-auto p-3 sm:p-4 space-y-2 sm:space-y-3">
              {messages.map((msg) => (
                <div
                  key={msg.id}
                  className={`flex ${msg.sender === 'user' ? 'justify-end' : 'justify-start'}`}
                >
                  <div
                    className={`max-w-[85%] p-2 sm:p-3 rounded-2xl ${
                      msg.sender === 'user'
                        ? 'bg-blue-600 text-white rounded-br-sm'
                        : 'bg-slate-700 text-slate-200 rounded-bl-sm'
                    }`}
                  >
                    {msg.sender === 'bot' ? (
                      <div dangerouslySetInnerHTML={{ __html: msg.text }} className="text-xs sm:text-sm" />
                    ) : (
                      <p className="text-xs sm:text-sm">{msg.text}</p>
                    )}
                    <p className="text-[8px] sm:text-[9px] opacity-50 mt-0.5 sm:mt-1">
                      {msg.timestamp.toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit' })}
                    </p>
                  </div>
                </div>
              ))}
              {isLoading && (
                <div className="flex justify-start">
                  <div className="bg-slate-700 p-2 sm:p-3 rounded-2xl rounded-bl-sm">
                    <div className="flex gap-1">
                      <span className="w-1.5 h-1.5 sm:w-2 sm:h-2 bg-slate-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                      <span className="w-1.5 h-1.5 sm:w-2 sm:h-2 bg-slate-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                      <span className="w-1.5 h-1.5 sm:w-2 sm:h-2 bg-slate-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
                    </div>
                  </div>
                </div>
              )}
              <div ref={messagesEndRef} />
            </div>

            {/* Input Area */}
            <div className="p-3 sm:p-4 border-t border-slate-700">
              <div className="flex gap-2">
                <input
                  ref={inputRef}
                  type="text"
                  value={inputValue}
                  onChange={(e) => setInputValue(e.target.value)}
                  onKeyPress={handleKeyPress}
                  placeholder="Tanyakan tentang data OTDR, klasifikasi, atau statistik..."
                  className="flex-1 px-3 sm:px-4 py-2 bg-slate-900 border border-slate-700 rounded-xl 
                    text-white text-xs sm:text-sm placeholder:text-slate-500 
                    focus:ring-2 focus:ring-blue-500/50 outline-none"
                />
                <button
                  onClick={sendMessage}
                  disabled={isLoading || !inputValue.trim()}
                  className="p-2 sm:p-2.5 bg-blue-600 hover:bg-blue-700 disabled:bg-slate-700 
                    rounded-xl transition-all"
                >
                  <Send className="w-4 h-4 sm:w-5 sm:h-5 text-white" />
                </button>
              </div>
              <p className="text-[8px] text-slate-500 mt-2 text-center">
                💡 Contoh: "Loss data pertama berapa?", "Batas Prx normal?", "Jumlah data bending?"
              </p>
            </div>
          </>
        )}
      </div>
    </div>
  );
};

export default Chatbot;