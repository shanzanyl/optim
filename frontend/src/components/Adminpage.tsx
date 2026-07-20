// frontend/src/components/Adminpage.tsx
import React, { useState, useEffect } from 'react';
import { CheckCircle, XCircle, RefreshCw } from 'lucide-react';

interface User {
  id: number;
  email: string;
  name: string;
  is_approved: boolean;
  is_admin: boolean;
  created_at: string;
}

const API_BASE = 'https://optim-api-ckfhb5heg3f3btgz.southeastasia-01.azurewebsites.net';

const Adminpage: React.FC = () => {
  const [pendingUsers, setPendingUsers] = useState<User[]>([]);
  const [allUsers, setAllUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<'pending' | 'all'>('pending');

  const fetchUsers = async () => {
    try {
      const token = localStorage.getItem('token');

      const pendingRes = await fetch(`${API_BASE}/api/admin/users`, {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      const pendingData = await pendingRes.json();
      setPendingUsers(pendingData.users || []);

      const allRes = await fetch(`${API_BASE}/api/admin/users/all`, {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      const allData = await allRes.json();
      setAllUsers(allData.users || []);

    } catch (error) {
      console.error('Error fetching users:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchUsers();
    const interval = setInterval(() => {
      fetchUsers();
    }, 10000);
    return () => clearInterval(interval);
  }, []);

  const handleApprove = async (userId: number, email: string) => {
    try {
      const token = localStorage.getItem('token');
      const response = await fetch(`${API_BASE}/api/admin/approve/${userId}`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}` }
      });
      if (response.ok) {
        alert(`✅ User ${email} berhasil disetujui`);
        await fetchUsers();
      } else {
        const error = await response.json();
        alert(`Gagal: ${error.detail || 'Unknown error'}`);
      }
    } catch (error) {
      console.error('Error approving user:', error);
      alert('Gagal menghubungi server');
    }
  };

  const handleReject = async (userId: number, email: string) => {
    if (confirm(`Hapus user ${email}?`)) {
      try {
        const token = localStorage.getItem('token');
        const response = await fetch(`${API_BASE}/api/admin/reject/${userId}`, {
          method: 'DELETE',
          headers: { 'Authorization': `Bearer ${token}` }
        });
        if (response.ok) {
          alert(`❌ User ${email} berhasil dihapus`);
          await fetchUsers();
        } else {
          const error = await response.json();
          alert(`Gagal: ${error.detail || 'Unknown error'}`);
        }
      } catch (error) {
        console.error('Error rejecting user:', error);
        alert('Gagal menghubungi server');
      }
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-[#14213d] flex items-center justify-center h-48 md:h-64">
        <RefreshCw className="w-6 h-6 md:w-8 md:h-8 text-blue-500 animate-spin" />
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#14213d] text-slate-300 font-sans">
      <div className="p-3 sm:p-4 md:p-6 space-y-4 md:space-y-6">
        {/* Header */}
        <div className="flex flex-wrap justify-between items-center gap-3">
          <div className="flex items-center gap-2">
            <h1 className="text-lg md:text-xl font-bold text-white">Admin Dashboard</h1>
          </div>
          <button
            onClick={fetchUsers}
            className="p-1.5 sm:p-2 bg-[#1e2f50] border border-[#3b4f6e] rounded-lg hover:bg-[#2a3d60] transition"
            title="Refresh"
          >
            <RefreshCw className="w-4 h-4 sm:w-5 sm:h-5 text-slate-400" />
          </button>
        </div>

        {/* Stats Cards */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 md:gap-4">
          <div className="bg-amber-500/10 border border-amber-500/20 rounded-xl p-3 md:p-4">
            <p className="text-[12px] md:text-sm text-amber-400 uppercase tracking-wider">Menunggu Persetujuan</p>
            <p className="text-xl md:text-2xl font-bold text-amber-400">{pendingUsers.length}</p>
          </div>
          <div className="bg-blue-500/10 border border-blue-500/20 rounded-xl p-3 md:p-4">
            <p className="text-[12px] md:text-sm text-blue-400 uppercase tracking-wider">Total Pengguna</p>
            <p className="text-xl md:text-2xl font-bold text-blue-400">{allUsers.length}</p>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex gap-1 sm:gap-2 border-b border-[#3b4f6e]">
          <button
            onClick={() => setActiveTab('pending')}
            className={`px-3 sm:px-4 py-1.5 sm:py-2 text-sm sm:text-sm font-medium transition ${
              activeTab === 'pending'
                ? 'text-blue-500 border-b-2 border-blue-500'
                : 'text-white hover:text-blue-400'
            }`}
          >
            Pending Approval ({pendingUsers.length})
          </button>
          <button
            onClick={() => setActiveTab('all')}
            className={`px-3 sm:px-4 py-1.5 sm:py-2 text-sm sm:text-sm font-medium transition ${
              activeTab === 'all'
                ? 'text-blue-500 border-b-2 border-blue-500'
                : 'text-white hover:text-blue-400'
            }`}
          >
            All Users ({allUsers.length})
          </button>
        </div>

        {/* Pending Users Table */}
        {activeTab === 'pending' && (
          <div className="bg-[#1e2f50] rounded-xl border border-[#3b4f6e] overflow-hidden">
            <div className="overflow-x-auto">
              <div className="min-w-[500px] md:min-w-0">
                <table className="w-full">
                  <thead className="bg-[#14213d] text-white text-[11px] md:text-xs uppercase">
                    <tr>
                      <th className="px-3 md:px-6 py-2 md:py-3 text-left">Nama</th>
                      <th className="px-3 md:px-6 py-2 md:py-3 text-left">Email</th>
                      <th className="px-3 md:px-6 py-2 md:py-3 text-left hidden sm:table-cell">Tanggal Daftar</th>
                      <th className="px-3 md:px-6 py-2 md:py-3 text-center">Aksi</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#3b4f6e]/50">
                    {pendingUsers.length === 0 ? (
                      <tr>
                        <td colSpan={4} className="px-3 md:px-6 py-6 md:py-8 text-center text-slate-500 text-md md:text-base">
                          Tidak ada user yang menunggu persetujuan
                        </td>
                      </tr>
                    ) : (
                      pendingUsers.map((user) => (
                        <tr key={user.id} className="hover:bg-[#2a3d60]/20 transition-colors">
                          <td className="px-3 md:px-6 py-2 md:py-4 text-white text-xs md:text-sm">
                            {user.name || '-'}
                          </td>
                          <td className="px-3 md:px-6 py-2 md:py-4 text-slate-300 text-xs md:text-sm break-all">
                            {user.email}
                          </td>
                          <td className="px-3 md:px-6 py-2 md:py-4 text-slate-400 text-[12px] md:text-xs hidden sm:table-cell">
                            {user.created_at ? new Date(user.created_at).toLocaleString() : '-'}
                          </td>
                          <td className="px-3 md:px-6 py-2 md:py-4">
                            <div className="flex items-center justify-center gap-2 md:gap-3">
                              <button
                                onClick={() => handleApprove(user.id, user.email)}
                                className="p-1.5 md:p-2 bg-emerald-500/20 text-emerald-400 rounded-lg hover:bg-emerald-500/30 transition"
                                title="Setujui"
                              >
                                <CheckCircle className="w-3.5 h-3.5 md:w-4 md:h-4" />
                              </button>
                              <button
                                onClick={() => handleReject(user.id, user.email)}
                                className="p-1.5 md:p-2 bg-red-500/20 text-red-400 rounded-lg hover:bg-red-500/30 transition"
                                title="Tolak"
                              >
                                <XCircle className="w-3.5 h-3.5 md:w-4 md:h-4" />
                              </button>
                            </div>
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}

        {/* All Users Table */}
        {activeTab === 'all' && (
          <div className="bg-[#1e2f50] rounded-xl border border-[#3b4f6e] overflow-hidden">
            <div className="overflow-x-auto">
              <div className="min-w-[550px] md:min-w-0">
                <table className="w-full">
                  <thead className="bg-[#14213d] text-white text-[11px] md:text-xs uppercase">
                    <tr>
                      <th className="px-3 md:px-6 py-2 md:py-3 text-left">Nama</th>
                      <th className="px-3 md:px-6 py-2 md:py-3 text-left">Email</th>
                      <th className="px-3 md:px-6 py-2 md:py-3 text-left">Status</th>
                      <th className="px-3 md:px-6 py-2 md:py-3 text-left">Role</th>
                      <th className="px-3 md:px-6 py-2 md:py-3 text-center">Aksi</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#3b4f6e]/50">
                    {allUsers.map((user) => (
                      <tr key={user.id} className="hover:bg-[#2a3d60]/20 transition-colors">
                        <td className="px-3 md:px-6 py-2 md:py-4 text-white text-xs md:text-sm">
                          {user.name || '-'}
                        </td>
                        <td className="px-3 md:px-6 py-2 md:py-4 text-slate-300 text-xs md:text-sm break-all">
                          {user.email}
                        </td>
                        <td className="px-3 md:px-6 py-2 md:py-4">
                          <span className={`px-1.5 md:px-2 py-0.5 md:py-1 rounded-full text-[9px] md:text-xs ${
                            user.is_approved
                              ? 'bg-emerald-500/20 text-emerald-400'
                              : 'bg-amber-500/20 text-amber-400'
                          }`}>
                            {user.is_approved ? 'Approved' : 'Pending'}
                          </span>
                        </td>
                        <td className="px-3 md:px-6 py-2 md:py-4">
                          <span className={`px-1.5 md:px-2 py-0.5 md:py-1 rounded-full text-[9px] md:text-xs ${
                            user.is_admin
                              ? 'bg-purple-500/20 text-purple-400'
                              : 'bg-slate-500/20 text-slate-400'
                          }`}>
                            {user.is_admin ? 'Admin' : 'User'}
                          </span>
                        </td>
                        <td className="px-3 md:px-6 py-2 md:py-4">
                          <div className="flex items-center justify-center gap-2 md:gap-3">
                            {!user.is_approved && (
                              <button
                                onClick={() => handleApprove(user.id, user.email)}
                                className="p-1.5 md:p-2 bg-emerald-500/20 text-emerald-400 rounded-lg hover:bg-emerald-500/30 transition"
                                title="Setujui"
                              >
                                <CheckCircle className="w-3.5 h-3.5 md:w-4 md:h-4" />
                              </button>
                            )}
                            {!user.is_admin && (
                              <button
                                onClick={() => handleReject(user.id, user.email)}
                                className="p-1.5 md:p-2 bg-red-500/20 text-red-400 rounded-lg hover:bg-red-500/30 transition"
                                title="Hapus User"
                              >
                                <XCircle className="w-3.5 h-3.5 md:w-4 md:h-4" />
                              </button>
                            )}
                            {user.is_admin && (
                              <span className="text-slate-500 text-xs">—</span>
                            )}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default Adminpage;