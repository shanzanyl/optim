// src/services/api.ts

export const API_BASE = 'https://optim-api-ckfhb5heg3f3btgz.southeastasia-01.azurewebsites.net';
export const API_URL = `${API_BASE}/api`;

// ============ FUNGSI AUTH ============

export async function login(email: string, password: string) {
  const response = await fetch(`${API_URL}/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
  
  const data = await response.json();
  
  if (response.ok && data.access_token) {
    localStorage.setItem('token', data.access_token);
    localStorage.setItem('userName', data.user?.name || 'User');
  }
  
  return data;
}

export async function register(email: string, password: string, name: string) {
  const response = await fetch(`${API_URL}/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password, name }),
  });
  
  return response.json();
}

export function logout() {
  localStorage.removeItem('token');
  localStorage.removeItem('userName');
}

export async function getDashboard(limit: number = 100) {
  const token = localStorage.getItem('token');
  const response = await fetch(`${API_URL}/dashboard?limit=${limit}`, {
    headers: { 'Authorization': `Bearer ${token}` },
  });
  
  return response.json();
}

export async function getHistory(limit: number = 50, skip: number = 0) {
  const token = localStorage.getItem('token');
  const response = await fetch(`${API_URL}/history?limit=${limit}&skip=${skip}`, {
    headers: { 'Authorization': `Bearer ${token}` },
  });
  
  return response.json();
}

export async function syncFromSheets() {
  const token = localStorage.getItem('token');
  const response = await fetch(`${API_URL}/sync`, {
    method: 'POST',
    headers: { 'Authorization': `Bearer ${token}` },
  });
  
  return response.json();
}

export async function uploadOtdrImage(file: File) {
  const token = localStorage.getItem('token');
  const formData = new FormData();
  formData.append('file', file);
  
  const response = await fetch(`${API_URL}/detect`, {
    method: 'POST',
    headers: { 'Authorization': `Bearer ${token}` },
    body: formData,
  });
  
  return response.json();
}