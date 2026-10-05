'use client';

import { useEffect, useState } from 'react';
import axios from 'axios';
import { UserList } from '@/components/users/UserList';
import { UserForm } from '@/components/users/UserForm';

interface User {
  id: number;
  username: string;
  email: string;
  full_name: string | null;
  created_at: string;
  updated_at: string;
}

export default function UsersPage() {
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [editingUser, setEditingUser] = useState<User | null>(null);

  const fetchUsers = async () => {
    try {
      setLoading(true);
      const response = await axios.get(`${process.env.NEXT_PUBLIC_API_URL}/api/users?limit=100`);
      setUsers(response.data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch users');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchUsers();
  }, []);

  const handleCreate = async (data: any) => {
    try {
      await axios.post(`${process.env.NEXT_PUBLIC_API_URL}/api/users`, data);
      setShowForm(false);
      fetchUsers();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create user');
    }
  };

  const handleUpdate = async (data: any) => {
    if (!editingUser) return;
    try {
      await axios.put(`${process.env.NEXT_PUBLIC_API_URL}/api/users/${editingUser.id}`, data);
      setEditingUser(null);
      fetchUsers();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update user');
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm('Are you sure you want to delete this user?')) return;
    try {
      await axios.delete(`${process.env.NEXT_PUBLIC_API_URL}/api/users/${id}`);
      fetchUsers();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete user');
    }
  };

  return (
    <div className="page-container">
      <div className="page-header">
        <h1>Users</h1>
        <button className="btn-primary" onClick={() => { setEditingUser(null); setShowForm(!showForm); }}>
          {showForm ? 'Cancel' : '+ Add User'}
        </button>
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      {showForm && (
        <UserForm
          onSubmit={handleCreate}
          onCancel={() => setShowForm(false)}
        />
      )}

      {editingUser && (
        <UserForm
          user={editingUser}
          onSubmit={handleUpdate}
          onCancel={() => setEditingUser(null)}
        />
      )}

      {loading ? (
        <div className="loading">Loading users...</div>
      ) : users.length === 0 ? (
        <div className="empty-state">No users found. Create one to get started!</div>
      ) : (
        <UserList
          users={users}
          onEdit={setEditingUser}
          onDelete={handleDelete}
        />
      )}
    </div>
  );
}
