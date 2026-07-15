import React, { useState } from 'react';
import { Mail, Lock, User, Eye, EyeOff, Loader2, Activity, TrendingUp } from 'lucide-react';
import backgroundImage from "../assets/background.webp";

interface OverviewProps {
  onLogin: (userData: any, isAdmin: boolean) => void;
}

const Overview = ({ onLogin }: OverviewProps) => {
  const [isLoginMode, setIsLoginMode] = useState(true);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [checkingStatus, setCheckingStatus] = useState(false);
  const [focusedField, setFocusedField] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setIsLoading(true);

    try {
      const endpoint = isLoginMode ? '/api/login' : '/api/register';
      const payload = isLoginMode 
        ? { email, password } 
        : { email, password, name };

      const response = await fetch(`https://optim-api-ckfhb5heg3f3btgz.southeastasia-01.azurewebsites.net${endpoint}`, {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          'Accept': 'application/json'
        },
        body: JSON.stringify(payload),
      });

      const data = await response.json();

      if (response.ok) {
        if (isLoginMode) {
          localStorage.setItem('token', data.access_token || data.token);
          const isAdminUser = data.user?.is_admin || false;
          onLogin(data.user, isAdminUser);
        } else {
          alert("✅ Akun berhasil dibuat! Silakan tunggu persetujuan admin.");
          setIsLoginMode(true);
          setPassword('');
        }
      } else {
        if (response.status === 403 || data.detail === "Akun belum disetujui admin. Silakan tunggu persetujuan.") {
          setError("⏳ AKUN ANDA BELUM DISETUJUI ADMIN. Silakan tunggu atau hubungi administrator.");
        } else {
          setError(data.message || data.detail || 'Gagal memproses permintaan');
        }
      }
    } catch (err) {
      setError('Server OptiM tidak merespons. Pastikan backend di port 8000 sudah jalan.');
    } finally {
      setIsLoading(false);
    }
  };

  const checkStatus = async () => {
    if (!email) {
      setError("Masukkan email terlebih dahulu");
      return;
    }
    setCheckingStatus(true);
    try {
      const response = await fetch(`https://optim-api-ckfhb5heg3f3btgz.southeastasia-01.azurewebsites.net/api/check-status?email=${encodeURIComponent(email)}`);
      const data = await response.json();
      if (data.is_approved) {
        setError("✅ AKUN ANDA SUDAH DISETUJUI! Silakan login.");
      } else {
        setError("⏳ AKUN ANDA MASIH MENUNGGU PERSETUJUAN ADMIN. Silakan tunggu atau hubungi administrator.");
      }
    } catch (err) {
      setError("Gagal mengecek status. Coba lagi nanti.");
    } finally {
      setCheckingStatus(false);
    }
  };

  return (
    <div className="min-h-screen relative overflow-hidden font-['Inter',system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif]">
      {/* Background Image */}
      <div 
        className="absolute inset-0 bg-cover bg-center bg-no-repeat"
        style={{ backgroundImage: `url(${backgroundImage})` }}
      >
        <div className="absolute inset-0 bg-gradient-to-br from-slate-900/80 via-slate-900/90 to-slate-900/95 backdrop-blur-[2px]"></div>
      </div>

      {/* Decorative Elements */}
      <div className="absolute top-20 -left-20 w-96 h-96 bg-blue-500/10 rounded-full blur-3xl pointer-events-none"></div>
      <div className="absolute bottom-20 -right-20 w-96 h-96 bg-cyan-500/10 rounded-full blur-3xl pointer-events-none"></div>

      <div className="relative z-10 min-h-screen flex items-center justify-center p-4 md:p-6 lg:p-8">
        <div className="max-w-6xl w-full">
          <div className="grid lg:grid-cols-2 gap-8 lg:gap-12 items-center">
            
            {/* Sisi Kiri: Branding - dengan ukuran responsif */}
<div className="block space-y-4 md:space-y-8 text-white">
  <div className="animate-in fade-in slide-in-from-left duration-700">
    {/* Logo - lebih kecil di HP */}
    <div className="flex items-center justify-center md:justify-start gap-2 md:gap-3 mb-4 md:mb-8">
      <div className="w-10 h-10 md:w-14 md:h-14 bg-gradient-to-br from-blue-600 to-blue-700 rounded-xl md:rounded-2xl flex items-center justify-center shadow-xl shadow-blue-600/30 ring-1 ring-white/10">
        <TrendingUp className="w-5 h-5 md:w-7 md:h-7 text-white" strokeWidth={1.5} />
      </div>
      <div className="text-center md:text-left">
        <h1 className="text-3xl md:text-5xl font-light tracking-wide text-white">
          Opti<span className="font-bold bg-gradient-to-r from-blue-400 to-cyan-400 bg-clip-text text-transparent">M</span>
        </h1>
        <p className="text-[11px] md:text-[15px] text-white font-medium tracking-[0.2em] uppercase mt-0.5">Optical Monitoring</p>
      </div>
    </div>

    {/* Hero Text - lebih kecil di HP */}
    <h2 className="text-lg md:text-3xl font-light leading-tight mb-2 md:mb-4 text-white text-center md:text-left">
      Sistem Monitoring & Klasifikasi
      <br />
      Gangguan <span className="font-semibold text-blue-400">Fiber Optic</span>
    </h2>
    
    <p className="text-slate-100 text-sm md:text-base leading-relaxed max-w-md text-center md:text-left mx-auto md:mx-0">
      Platform berbasis Machine Learning untuk mengklasifikasikan 
      gangguan pada jaringan fiber optic secara akurat.
    </p>
  </div>
</div>

            {/* Sisi Kanan: Auth Card - TIDAK BERUBAH SAMA SEKALI */}
            <div className="w-full max-w-md mx-auto lg:max-w-none">
              <div className="bg-white/5 backdrop-blur-xl border border-white/10 rounded-2xl shadow-2xl overflow-hidden animate-in zoom-in duration-500">
                
                {/* Card Header gradient line */}
                <div className="h-1 bg-gradient-to-r from-blue-500 via-blue-600 to-blue-500"></div>
                
                <div className="p-6 md:p-8">
                  {/* Title Section */}
                  <div className="mb-8 text-center">
                    <h3 className="text-2xl font-semibold text-white mb-2 tracking-tight">
                      {isLoginMode ? 'Welcome Back' : 'Create New Account'}
                    </h3>
                    <p className="text-white text-md opacity-80">
                      {isLoginMode 
                        ? 'Sign in to access your monitoring dashboard' 
                        : 'Sign up to start monitoring your fiber network'}
                    </p>
                  </div>

                  {/* Toggle Switch */}
                  <div className="flex gap-1 mb-8 bg-slate-800/50 rounded-xl p-1">
                    <button
                      onClick={() => { setIsLoginMode(true); setError(''); setEmail(''); setPassword(''); setName(''); }}
                      className={`flex-1 py-2.5 rounded-lg text-md font-medium transition-all duration-200 ${
                        isLoginMode 
                          ? 'bg-blue-600 text-white shadow-lg shadow-blue-600/25' 
                          : 'text-slate-400 hover:text-slate-200'
                      }`}
                    >
                      Login
                    </button>
                    <button
                      onClick={() => { setIsLoginMode(false); setError(''); setEmail(''); setPassword(''); setName(''); }}
                      className={`flex-1 py-2.5 rounded-lg text-md font-medium transition-all duration-200 ${
                        !isLoginMode 
                          ? 'bg-blue-600 text-white shadow-lg shadow-blue-600/25' 
                          : 'text-slate-400 hover:text-slate-200'
                      }`}
                    >
                      Sign Up
                    </button>
                  </div>

                  <form onSubmit={handleSubmit} className="space-y-5">
                    {/* Nama Lengkap */}
                    {!isLoginMode && (
                      <div className="space-y-1.5">
                        <label className="text-sm font-medium text-white ml-1">Full Name</label>
                        <div className={`relative transition-all duration-200 ${
                          focusedField === 'name' ? 'scale-[1.01]' : ''
                        }`}>
                          <User className={`absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 transition-colors ${
                            focusedField === 'name' ? 'text-blue-400' : 'text-slate-500'
                          }`} />
                          <input
                            type="text"
                            required
                            value={name}
                            onChange={(e) => setName(e.target.value)}
                            onFocus={() => setFocusedField('name')}
                            onBlur={() => setFocusedField(null)}
                            className="w-full pl-10 pr-4 py-3 bg-slate-900/60 border rounded-xl text-white placeholder:text-slate-500 outline-none transition-all duration-200 text-sm"
                            style={{
                              borderColor: focusedField === 'name' ? 'rgba(59,130,246,0.5)' : 'rgba(255,255,255,0.1)',
                              boxShadow: focusedField === 'name' ? '0 0 0 3px rgba(59,130,246,0.1)' : 'none'
                            }}
                            placeholder="Enter your full name"
                          />
                        </div>
                      </div>
                    )}


                    {/* Email Field */}
                    <div className="space-y-1.5">
                      <label className="text-sm font-medium text-white ml-1">Email Address</label>
                      <div className={`relative transition-all duration-200 ${
                        focusedField === 'email' ? 'scale-[1.01]' : ''
                      }`}>
                        <Mail className={`absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 transition-colors ${
                          focusedField === 'email' ? 'text-blue-400' : 'text-slate-500'
                        }`} />
                        <input
                          type="email"
                          required
                          value={email}
                          onChange={(e) => setEmail(e.target.value)}
                          onFocus={() => setFocusedField('email')}
                          onBlur={() => setFocusedField(null)}
                          className="w-full pl-10 pr-4 py-3 bg-slate-900/60 border rounded-xl text-white placeholder:text-slate-500 outline-none transition-all duration-200 text-sm"
                          style={{
                            borderColor: focusedField === 'email' ? 'rgba(59,130,246,0.5)' : 'rgba(255,255,255,0.1)',
                            boxShadow: focusedField === 'email' ? '0 0 0 3px rgba(59,130,246,0.1)' : 'none'
                          }}
                          placeholder="Enter your email address"
                        />
                      </div>
                    </div>

                    {/* Password Field */}
                    <div className="space-y-1.5">
                      <label className="text-sm font-medium text-white ml-1">Password</label>
                      <div className={`relative transition-all duration-200 ${
                        focusedField === 'password' ? 'scale-[1.01]' : ''
                      }`}>
                        <Lock className={`absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 transition-colors ${
                          focusedField === 'password' ? 'text-blue-400' : 'text-slate-500'
                        }`} />
                        <input
                          type={showPassword ? 'text' : 'password'}
                          required
                          value={password}
                          onChange={(e) => setPassword(e.target.value)}
                          onFocus={() => setFocusedField('password')}
                          onBlur={() => setFocusedField(null)}
                          className="w-full pl-10 pr-12 py-3 bg-slate-900/60 border rounded-xl text-white placeholder:text-slate-500 outline-none transition-all duration-200 text-sm"
                          style={{
                            borderColor: focusedField === 'password' ? 'rgba(59,130,246,0.5)' : 'rgba(255,255,255,0.1)',
                            boxShadow: focusedField === 'password' ? '0 0 0 3px rgba(59,130,246,0.1)' : 'none'
                          }}
                          placeholder="Fill in your password"
                        />
                        {/* PERBAIKAN: Tombol show/hide password dengan aria-label dan touch target yang cukup */}
                        <button
                          type="button"
                          onClick={() => setShowPassword(!showPassword)}
                          aria-label={showPassword ? "Sembunyikan kata sandi" : "Tampilkan kata sandi"}
                          className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300 transition-colors p-2 min-w-[44px] min-h-[44px] flex items-center justify-center"
                        >
                          {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                        </button>
                      </div>
                    </div>

                    {/* Error Message */}
                    {error && (
                      <div className={`rounded-xl p-3 animate-in fade-in duration-200 ${
                        error.includes('Already approved') 
                          ? 'bg-emerald-500/10 border border-emerald-500/20' 
                          : error.includes('Not yet approved') 
                          ? 'bg-amber-500/10 border border-amber-500/20'
                          : 'bg-red-500/10 border border-red-500/20'
                      }`}>
                        <p className={`text-sm font-medium text-center ${
                          error.includes('Already approved') 
                            ? 'text-emerald-400' 
                            : error.includes('Not yet approved') 
                            ? 'text-amber-400'
                            : 'text-red-400'
                        }`}>{error}</p>
                      </div>
                    )}

                    {/* Submit Button */}
                    <button
                      type="submit"
                      disabled={isLoading}
                      className="w-full py-3.5 bg-gradient-to-r from-blue-600 to-blue-700 hover:from-blue-500 hover:to-blue-600 text-white font-medium rounded-xl transition-all duration-200 shadow-lg shadow-blue-600/20 flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed active:scale-[0.98]"
                    >
                      {isLoading ? (
                        <>
                          <Loader2 className="w-5 h-5 animate-spin" />
                          <span>Processing...</span>
                        </>
                      ) : (
                        isLoginMode ? (
                          <>
                            <Activity className="w-4 h-4" />
                            <span>Sign In to Dashboard</span>
                          </>
                        ) : (
                          <>
                            <User className="w-4 h-4" />
                            <span>Sign Up Now</span>
                          </>
                        )
                      )}
                    </button>

                    {/* Additional Links */}
                    {isLoginMode && (
                      <div className="text-center pt-2">
                        <button
                          type="button"
                          onClick={checkStatus}
                          disabled={checkingStatus}
                          className="text-sm text-white hover:text-blue-400 transition-colors inline-flex items-center gap-1"
                        >
                          {checkingStatus ? (
                            <>
                              <Loader2 className="w-3 h-3 animate-spin" />
                              <span>Checking...</span>
                            </>
                          ) : (
                            <>
                              <span></span>
                              <span>Cannot login? Check account status</span>
                            </>
                          )}
                        </button>
                      </div>
                    )}
                  </form>

                  {/* Footer text */}
                  <p className="text-center text-[15px] text-white mt-6">
                    {isLoginMode 
                      ? 'Use your registered email and password to access the dashboard' 
                      : 'Make sure you fill in the data correctly' 
                      }
                  </p>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default Overview;