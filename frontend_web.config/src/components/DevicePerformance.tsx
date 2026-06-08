import { useState, useEffect } from 'react';
import { Server, Activity, HardDrive, Cpu as CpuIcon } from 'lucide-react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';

interface PerformanceData {
  time: string;
  memory: number;
  cpu: number;
}

const DevicePerformance = () => {
  const [performanceData, setPerformanceData] = useState<PerformanceData[]>([
    { time: '16:50', memory: 2.1, cpu: 1.5 },
    { time: '16:55', memory: 2.3, cpu: 1.8 },
    { time: '17:00', memory: 2.5, cpu: 2.1 },
    { time: '17:05', memory: 3.2, cpu: 2.5 },
    { time: '17:10', memory: 6.5, cpu: 3.2 },
    { time: '17:15', memory: 3.8, cpu: 2.8 },
    { time: '17:20', memory: 3.5, cpu: 2.3 },
    { time: '17:25', memory: 3.2, cpu: 2.0 },
    { time: '17:30', memory: 4.1, cpu: 2.6 },
    { time: '17:35', memory: 3.6, cpu: 2.2 },
    { time: '17:40', memory: 6.2, cpu: 5.5 },
    { time: '17:45', memory: 5.8, cpu: 6.2 },
    { time: '17:50', memory: 6.5, cpu: 6.8 },
  ]);

  const [currentMemory, setCurrentMemory] = useState(4.2);
  const [currentCPU, setCurrentCPU] = useState(3.5);
  const [memoryPercent, setMemoryPercent] = useState(52.5);
  const [cpuPercent, setCpuPercent] = useState(43.8);

  // Simulate real-time data updates
  useEffect(() => {
    const interval = setInterval(() => {
      setPerformanceData(prev => {
        const newData = [...prev.slice(1)];
        const lastTime = prev[prev.length - 1].time;
        const [hour, minute] = lastTime.split(':').map(Number);
        const newMinute = minute + 5;
        const newHour = newMinute >= 60 ? hour + 1 : hour;
        const finalMinute = newMinute >= 60 ? newMinute - 60 : newMinute;
        const newTime = `${newHour}:${finalMinute.toString().padStart(2, '0')}`;
        
        const newMemory = Math.random() * 4 + 2;
        const newCPU = Math.random() * 5 + 1;
        
        newData.push({
          time: newTime,
          memory: parseFloat(newMemory.toFixed(1)),
          cpu: parseFloat(newCPU.toFixed(1)),
        });
        
        setCurrentMemory(parseFloat(newMemory.toFixed(1)));
        setCurrentCPU(parseFloat(newCPU.toFixed(1)));
        setMemoryPercent(parseFloat(((newMemory / 8) * 100).toFixed(1)));
        setCpuPercent(parseFloat(((newCPU / 8) * 100).toFixed(1)));
        
        return newData;
      });
    }, 3000);

    return () => clearInterval(interval);
  }, []);

  const maxMemory = 8;
  const maxCPU = 8;

  const GaugeChart = ({ value, max, label, color, unit }: { value: number; max: number; label: string; color: string; unit: string }) => {
    const percentage = (value / max) * 100;
    const rotation = -90 + (percentage / 100) * 180;

    return (
      <div className="flex flex-col items-center">
        <div className="relative w-48 h-24">
          <svg viewBox="0 0 200 100" className="w-full h-full">
            {/* Background arc */}
            <path
              d="M 20 90 A 80 80 0 0 1 180 90"
              fill="none"
              stroke="#1e293b"
              strokeWidth="16"
              strokeLinecap="round"
            />
            {/* Value arc with gradient */}
            <defs>
              <linearGradient id={`gradient-${label}`} x1="0%" y1="0%" x2="100%" y2="0%">
                <stop offset="0%" stopColor={color} stopOpacity={0.5} />
                <stop offset="100%" stopColor={color} stopOpacity={1} />
              </linearGradient>
            </defs>
            <path
              d="M 20 90 A 80 80 0 0 1 180 90"
              fill="none"
              stroke={`url(#gradient-${label})`}
              strokeWidth="16"
              strokeLinecap="round"
              strokeDasharray={`${(percentage / 100) * 251.2} 251.2`}
            />
            {/* Needle */}
            <g transform={`rotate(${rotation}, 100, 90)`}>
              <line
                x1="100"
                y1="90"
                x2="100"
                y2="30"
                stroke="#ffffff"
                strokeWidth="3"
                strokeLinecap="round"
              />
              <circle cx="100" cy="90" r="5" fill="#ffffff" />
            </g>
          </svg>
        </div>
        <div className="mt-3 text-center">
          <p className="text-3xl font-bold text-white">
            {value} <span className="text-lg text-slate-400">{unit}</span>
          </p>
          <p className="text-sm text-slate-400 mt-1">{label}</p>
          <p className="text-xs text-slate-500 mt-1">{percentage.toFixed(1)}% of {max}{unit}</p>
        </div>
      </div>
    );
  };

  return (
    <div className="space-y-6">
      {/* Stats Overview */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-6">
        <div className="bg-gradient-to-br from-blue-500/10 to-blue-600/5 border border-blue-500/20 rounded-xl p-6">
          <div className="flex items-center justify-between mb-3">
            <div className="w-10 h-10 bg-blue-500/20 rounded-lg flex items-center justify-center">
              <Server className="w-5 h-5 text-blue-400" />
            </div>
            <span className="text-xs px-2 py-1 bg-green-500/20 text-green-400 rounded-full">Online</span>
          </div>
          <p className="text-slate-400 text-sm">Device Status</p>
          <p className="text-xl font-bold text-white mt-1">Active</p>
        </div>

        <div className="bg-gradient-to-br from-purple-500/10 to-purple-600/5 border border-purple-500/20 rounded-xl p-6">
          <div className="flex items-center justify-between mb-3">
            <div className="w-10 h-10 bg-purple-500/20 rounded-lg flex items-center justify-center">
              <HardDrive className="w-5 h-5 text-purple-400" />
            </div>
          </div>
          <p className="text-slate-400 text-sm">Memory Usage</p>
          <p className="text-xl font-bold text-white mt-1">{memoryPercent}%</p>
        </div>

        <div className="bg-gradient-to-br from-cyan-500/10 to-cyan-600/5 border border-cyan-500/20 rounded-xl p-6">
          <div className="flex items-center justify-between mb-3">
            <div className="w-10 h-10 bg-cyan-500/20 rounded-lg flex items-center justify-center">
              <CpuIcon className="w-5 h-5 text-cyan-400" />
            </div>
          </div>
          <p className="text-slate-400 text-sm">CPU Usage</p>
          <p className="text-xl font-bold text-white mt-1">{cpuPercent}%</p>
        </div>

        <div className="bg-gradient-to-br from-green-500/10 to-green-600/5 border border-green-500/20 rounded-xl p-6">
          <div className="flex items-center justify-between mb-3">
            <div className="w-10 h-10 bg-green-500/20 rounded-lg flex items-center justify-center">
              <Activity className="w-5 h-5 text-green-400" />
            </div>
          </div>
          <p className="text-slate-400 text-sm">Uptime</p>
          <p className="text-xl font-bold text-white mt-1">99.9%</p>
        </div>
      </div>

      {/* Memory & CPU Traffic Chart */}
      <div className="bg-slate-800/50 backdrop-blur-sm border border-slate-700/50 rounded-xl p-6">
        <h3 className="text-lg font-semibold text-white mb-4">Memory / CPU Usage Over Time</h3>
        <div className="h-96">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={performanceData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
              <XAxis 
                dataKey="time" 
                stroke="#94a3b8" 
                style={{ fontSize: '12px' }}
              />
              <YAxis 
                stroke="#94a3b8" 
                style={{ fontSize: '12px' }}
                label={{ value: 'GB / %', angle: -90, position: 'insideLeft', fill: '#94a3b8' }}
              />
              <Tooltip
                contentStyle={{
                  backgroundColor: '#1e293b',
                  border: '1px solid #334155',
                  borderRadius: '8px',
                  color: '#fff',
                }}
                formatter={(value: number) => [`${value} GB`, '']}
              />
              <Legend />
              <Line
                type="monotone"
                dataKey="memory"
                stroke="#3b82f6"
                strokeWidth={3}
                dot={{ fill: '#3b82f6', r: 4 }}
                activeDot={{ r: 6 }}
                name="Memory"
              />
              <Line
                type="monotone"
                dataKey="cpu"
                stroke="#ef4444"
                strokeWidth={3}
                dot={{ fill: '#ef4444', r: 4 }}
                activeDot={{ r: 6 }}
                name="CPU"
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Gauge Meters */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Memory Gauge */}
        <div className="bg-slate-800/50 backdrop-blur-sm border border-slate-700/50 rounded-xl p-8">
          <div className="flex items-center gap-3 mb-6">
            <div className="w-10 h-10 bg-blue-500/20 rounded-lg flex items-center justify-center">
              <HardDrive className="w-5 h-5 text-blue-400" />
            </div>
            <h3 className="text-lg font-semibold text-white">Memory Usage</h3>
          </div>
          <div className="flex justify-center items-center py-6">
            <GaugeChart
              value={currentMemory}
              max={maxMemory}
              label="Memory"
              color="#3b82f6"
              unit=" GB"
            />
          </div>
          <div className="mt-6 pt-6 border-t border-slate-700">
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div>
                <p className="text-slate-400">Used</p>
                <p className="text-white font-semibold">{currentMemory} GB</p>
              </div>
              <div>
                <p className="text-slate-400">Available</p>
                <p className="text-white font-semibold">{(maxMemory - currentMemory).toFixed(1)} GB</p>
              </div>
            </div>
          </div>
        </div>

        {/* CPU Gauge */}
        <div className="bg-slate-800/50 backdrop-blur-sm border border-slate-700/50 rounded-xl p-8">
          <div className="flex items-center gap-3 mb-6">
            <div className="w-10 h-10 bg-red-500/20 rounded-lg flex items-center justify-center">
              <CpuIcon className="w-5 h-5 text-red-400" />
            </div>
            <h3 className="text-lg font-semibold text-white">CPU Usage</h3>
          </div>
          <div className="flex justify-center items-center py-6">
            <GaugeChart
              value={currentCPU}
              max={maxCPU}
              label="CPU"
              color="#ef4444"
              unit=" GHz"
            />
          </div>
          <div className="mt-6 pt-6 border-t border-slate-700">
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div>
                <p className="text-slate-400">Current Load</p>
                <p className="text-white font-semibold">{currentCPU} GHz</p>
              </div>
              <div>
                <p className="text-slate-400">Max Capacity</p>
                <p className="text-white font-semibold">{maxCPU} GHz</p>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* System Information */}
      <div className="bg-slate-800/50 backdrop-blur-sm border border-slate-700/50 rounded-xl p-6">
        <h3 className="text-lg font-semibold text-white mb-4">System Information</h3>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          <div>
            <p className="text-slate-400 text-sm mb-1">Processor</p>
            <p className="text-white font-medium">Intel Xeon E5-2680 v4</p>
          </div>
          <div>
            <p className="text-slate-400 text-sm mb-1">Total Memory</p>
            <p className="text-white font-medium">{maxMemory} GB DDR4</p>
          </div>
          <div>
            <p className="text-slate-400 text-sm mb-1">Operating System</p>
            <p className="text-white font-medium">Linux Ubuntu 22.04 LTS</p>
          </div>
          <div>
            <p className="text-slate-400 text-sm mb-1">Network Interface</p>
            <p className="text-white font-medium">10 Gbps Ethernet</p>
          </div>
          <div>
            <p className="text-slate-400 text-sm mb-1">Monitoring Since</p>
            <p className="text-white font-medium">Jan 30, 2026 00:00</p>
          </div>
          <div>
            <p className="text-slate-400 text-sm mb-1">Last Updated</p>
            <p className="text-white font-medium">Real-time</p>
          </div>
        </div>
      </div>
    </div>
  );
};

export default DevicePerformance;
