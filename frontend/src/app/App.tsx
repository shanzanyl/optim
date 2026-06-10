import { useState, useEffect } from 'react';
import { BrowserRouter as Router } from 'react-router-dom'; 
import { 
  Zap,
  Search,
  LogOut, 
  Shield, 
  TrendingUp
} from 'lucide-react';
import MainDashboard from "../components/MainDashboard";
import Detection from "../components/Detection";
import Overview from "../components/Overview";
import Adminpage from "../components/Adminpage";
import Chatbot from "../components/chatbot";
import { SlideProvider } from '../Context/SlideContext';

interface UserData {
  id?: number;
  name: string;
  email: string;
  is_admin?: boolean;
}

export default function App() {
  const [activeTab, setActiveTab] = useState<'main' | 'detection' | 'admin'>('main');
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [user, setUser] = useState<UserData | null>(null);
  const [isAdmin, setIsAdmin] = useState(false);
  
  const [dashboardCurrentIndex, setDashboardCurrentIndex] = useState(0);
  const [detectionCurrentIndex, setDetectionCurrentIndex] = useState(0);
  const [refreshTrigger, setRefreshTrigger] = useState(0);

  useEffect(() => {
    const savedDashboardIndex = localStorage.getItem('dashboard_current_index');
    const savedDetectionIndex = localStorage.getItem('detection_current_index');
    
    if (savedDashboardIndex) setDashboardCurrentIndex(parseInt(savedDashboardIndex));
    if (savedDetectionIndex) setDetectionCurrentIndex(parseInt(savedDetectionIndex));
  }, []);

  useEffect(() => {
    localStorage.setItem('dashboard_current_index', dashboardCurrentIndex.toString());
  }, [dashboardCurrentIndex]);

  useEffect(() => {
    localStorage.setItem('detection_current_index', detectionCurrentIndex.toString());
  }, [detectionCurrentIndex]);

  useEffect(() => {
    const token = localStorage.getItem('token');
    const savedUser = localStorage.getItem('user');
    const savedIsAdmin = localStorage.getItem('isAdmin') === 'true';
    
    if (token && savedUser) {
      fetch('https://optim-api-ckfhb5heg3f3btgz.southeastasia-01.azurewebsites.net/api/me', {
        headers: { 'Authorization': `Bearer ${token}` }
      })
      .then(res => {
        if (res.ok) {
          return res.json();
        }
        throw new Error('Invalid token');
      })
      .then(userData => {
        setIsAuthenticated(true);
        setUser(userData);
        setIsAdmin(userData.is_admin || savedIsAdmin);
        localStorage.setItem('user', JSON.stringify(userData));
        localStorage.setItem('isAdmin', String(userData.is_admin || savedIsAdmin));
      })
      .catch(() => {
        localStorage.removeItem('token');
        localStorage.removeItem('user');
        localStorage.removeItem('isAdmin');
        setIsAuthenticated(false);
      });
    }
  }, []);

  const handleLogin = (userData: UserData, isAdminUser: boolean = false) => {
    setIsAuthenticated(true);
    setUser(userData);
    setIsAdmin(isAdminUser);
    localStorage.setItem('user', JSON.stringify(userData));
    localStorage.setItem('isAdmin', String(isAdminUser));
  };

  const handleLogout = () => {
    localStorage.removeItem('token');
    localStorage.removeItem('user');
    localStorage.removeItem('isAdmin');
    localStorage.removeItem('dashboard_current_index');
    localStorage.removeItem('detection_current_index');
    setIsAuthenticated(false);
    setUser(null);
    setIsAdmin(false);
    setActiveTab('main');
  };

  const handleDataChange = () => {
    setRefreshTrigger(prev => prev + 1);
  };

  if (!isAuthenticated) {
    return <Overview onLogin={handleLogin} />;
  }

  return (
    <SlideProvider>
      <Router>
        <div className="min-h-screen bg-[#14213d] font-['Poppins','Inter',system-ui,-apple-system,sans-serif']">
          
          {/* Header - warna disamakan dengan Dashboard */}
          <header className="bg-[#1e2f50] border-b border-[#3b4f6e] sticky top-0 z-50">
            <div className="w-full px-3 sm:px-4 md:px-6 lg:px-8 py-2 sm:py-3 md:py-4">
              <div className="flex items-center justify-between gap-2">
                
                {/* ========== LOGO ========== */}
                <div className="flex items-center gap-2 sm:gap-3 flex-shrink-0">
                  <div className="w-8 h-8 sm:w-10 sm:h-10 md:w-12 md:h-12 bg-gradient-to-br from-blue-600 to-blue-700 rounded-lg sm:rounded-xl flex items-center justify-center shadow-lg">
                    <TrendingUp className="w-4 h-4 sm:w-5 sm:h-5 md:w-6 md:h-6 text-white" strokeWidth={1.5} />
                  </div>
                  <div>
                    <h1 className="text-base sm:text-xl md:text-2xl font-light tracking-wide text-white leading-tight">
                      Opti<span className="font-bold bg-gradient-to-r from-blue-400 to-cyan-400 bg-clip-text text-transparent">M</span>
                    </h1>
                    <p className="text-[6px] sm:text-[8px] md:text-[9px] lg:text-[10px] text-slate-400 font-medium tracking-[0.15em] uppercase mt-0.5">
                      Optical Monitoring
                    </p>
                  </div>
                </div>
                
                {/* ========== NAVIGATION BUTTONS + USER ========== */}
                <div className="flex items-center gap-1 sm:gap-2 md:gap-3">
                  
                  {/* Navigation Buttons */}
                  <nav className="flex gap-0.5 sm:gap-1 bg-[#14213d]/50 rounded-lg sm:rounded-xl p-0.5 sm:p-1">
                    <button
                      onClick={() => setActiveTab('main')}
                      className={`flex items-center gap-1 px-2 sm:px-3 md:px-4 py-1.5 sm:py-2 rounded-md sm:rounded-lg text-xs sm:text-sm font-semibold transition-all ${
                        activeTab === 'main' 
                          ? 'bg-blue-600 text-white shadow-lg shadow-blue-600/25' 
                          : 'text-slate-300 hover:text-white hover:bg-[#2a3d60]'
                      }`}
                    >
                      <Zap className="w-3.5 h-3.5 sm:w-4 sm:h-4" />
                      <span className="hidden sm:inline">Dashboard</span>
                    </button>

                    <button
                      onClick={() => setActiveTab('detection')}
                      className={`flex items-center gap-1 px-2 sm:px-3 md:px-4 py-1.5 sm:py-2 rounded-md sm:rounded-lg text-xs sm:text-sm font-semibold transition-all ${
                        activeTab === 'detection' 
                          ? 'bg-blue-600 text-white shadow-lg shadow-blue-600/25' 
                          : 'text-slate-300 hover:text-white hover:bg-[#2a3d60]'
                      }`}
                    >
                      <Search className="w-3.5 h-3.5 sm:w-4 sm:h-4" />
                      <span className="hidden sm:inline">Detection</span>
                    </button>

                    {isAdmin && (
                      <button
                        onClick={() => setActiveTab('admin')}
                        className={`flex items-center gap-1 px-2 sm:px-3 md:px-4 py-1.5 sm:py-2 rounded-md sm:rounded-lg text-xs sm:text-sm font-semibold transition-all ${
                          activeTab === 'admin' 
                            ? 'bg-purple-600 text-white shadow-lg shadow-purple-600/25' 
                            : 'text-slate-300 hover:text-white hover:bg-[#2a3d60]'
                        }`}
                      >
                        <Shield className="w-3.5 h-3.5 sm:w-4 sm:h-4" />
                        <span className="hidden sm:inline">Admin</span>
                      </button>
                    )}
                  </nav>

                  {/* ========== USER INFO ========== */}
                  <div className="flex items-center gap-1 sm:gap-2 md:gap-3 pl-2 sm:pl-3 md:pl-4 border-l border-[#3b4f6e]">
                    <div className="text-right hidden sm:block">
                      <p className="text-xs sm:text-sm font-semibold text-white leading-tight truncate max-w-[100px] sm:max-w-[120px]">
                        {user?.name || 'User'}
                      </p>
                      <p className="text-[9px] sm:text-[10px] text-slate-400 mt-0.5 hidden md:block">{user?.email || ''}</p>
                      {isAdmin && (
                        <span className="inline-block mt-0.5 px-1 sm:px-1.5 py-0.5 bg-purple-500/20 rounded-md text-[7px] sm:text-[8px] font-semibold text-purple-400 uppercase tracking-wider">
                          Admin
                        </span>
                      )}
                    </div>
                    
                    {/* Avatar */}
                    <div className={`w-7 h-7 sm:w-8 sm:h-8 md:w-9 md:h-9 lg:w-10 lg:h-10 rounded-lg sm:rounded-xl flex items-center justify-center text-[10px] sm:text-xs md:text-sm font-bold text-white shadow-lg ${
                      isAdmin 
                        ? 'bg-gradient-to-br from-purple-500 to-purple-700' 
                        : 'bg-gradient-to-br from-blue-500 to-blue-700'
                    }`}>
                      {user?.name?.substring(0, 2).toUpperCase() || 'US'}
                    </div>

                    {/* Logout Button */}
                    <button 
                      onClick={handleLogout} 
                      className="w-7 h-7 sm:w-8 sm:h-8 md:w-9 md:h-9 bg-red-500/10 hover:bg-red-500/20 rounded-lg sm:rounded-xl flex items-center justify-center transition-all"
                      title="Logout"
                    >
                      <LogOut className="w-3.5 h-3.5 sm:w-4 sm:h-4 text-red-400" />
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </header>

          <main className="w-full animate-in fade-in duration-500">
            {activeTab === 'main' && (
              <MainDashboard 
                refreshTrigger={refreshTrigger}
                onDataChange={handleDataChange}
              />
            )}
            {activeTab === 'detection' && (
              <Detection 
                refreshTrigger={refreshTrigger}
                onDataChange={handleDataChange}
              />
            )}
            {activeTab === 'admin' && isAdmin && <Adminpage />}
          </main>

          <Chatbot />
        </div>
      </Router>
    </SlideProvider>
  );
}