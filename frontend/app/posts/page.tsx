'use client';

import { useEffect, useState } from 'react';
import axios from 'axios';
import { PostList } from '@/components/posts/PostList';
import { PostForm } from '@/components/posts/PostForm';

interface Post {
  id: number;
  title: string;
  content: string;
  user_id: number;
  created_at: string;
  updated_at: string;
}

interface User {
  id: number;
  username: string;
}

export default function PostsPage() {
  const [posts, setPosts] = useState<Post[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [editingPost, setEditingPost] = useState<Post | null>(null);

  const fetchData = async () => {
    try {
      setLoading(true);
      const [postsRes, usersRes] = await Promise.all([
        axios.get(`${process.env.NEXT_PUBLIC_API_URL}/api/posts?limit=100`),
        axios.get(`${process.env.NEXT_PUBLIC_API_URL}/api/users?limit=100`),
      ]);
      setPosts(postsRes.data);
      setUsers(usersRes.data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch data');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  const handleCreate = async (data: any) => {
    try {
      await axios.post(`${process.env.NEXT_PUBLIC_API_URL}/api/posts`, data);
      setShowForm(false);
      fetchData();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create post');
    }
  };

  const handleUpdate = async (data: any) => {
    if (!editingPost) return;
    try {
      await axios.put(`${process.env.NEXT_PUBLIC_API_URL}/api/posts/${editingPost.id}`, data);
      setEditingPost(null);
      fetchData();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update post');
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm('Are you sure you want to delete this post?')) return;
    try {
      await axios.delete(`${process.env.NEXT_PUBLIC_API_URL}/api/posts/${id}`);
      fetchData();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete post');
    }
  };

  return (
    <div className="page-container">
      <div className="page-header">
        <h1>Posts</h1>
        <button className="btn-primary" onClick={() => { setEditingPost(null); setShowForm(!showForm); }}>
          {showForm ? 'Cancel' : '+ New Post'}
        </button>
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      {showForm && (
        <PostForm
          users={users}
          onSubmit={handleCreate}
          onCancel={() => setShowForm(false)}
        />
      )}

      {editingPost && (
        <PostForm
          post={editingPost}
          users={users}
          onSubmit={handleUpdate}
          onCancel={() => setEditingPost(null)}
        />
      )}

      {loading ? (
        <div className="loading">Loading posts...</div>
      ) : posts.length === 0 ? (
        <div className="empty-state">No posts found. Create one to get started!</div>
      ) : (
        <PostList
          posts={posts}
          users={users}
          onEdit={setEditingPost}
          onDelete={handleDelete}
        />
      )}
    </div>
  );
}
