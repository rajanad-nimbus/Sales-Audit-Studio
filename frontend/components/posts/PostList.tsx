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

interface PostListProps {
  posts: Post[];
  users: User[];
  onEdit: (post: Post) => void;
  onDelete: (id: number) => void;
}

export function PostList({ posts, users, onEdit, onDelete }: PostListProps) {
  const getUserName = (userId: number) => {
    return users.find((u) => u.id === userId)?.username || 'Unknown';
  };

  return (
    <div className="posts-grid">
      {posts.map((post) => (
        <div key={post.id} className="post-card">
          <div className="card-header">
            <h3>{post.title}</h3>
            <div className="post-meta">
              <span className="author">by {getUserName(post.user_id)}</span>
              <span className="date">{new Date(post.created_at).toLocaleDateString()}</span>
            </div>
          </div>

          <div className="card-content">
            <p className="post-excerpt">{post.content}</p>
          </div>

          <div className="card-footer">
            <button
              className="btn-small btn-secondary"
              onClick={() => onEdit(post)}
            >
              Edit
            </button>
            <button
              className="btn-small btn-danger"
              onClick={() => onDelete(post.id)}
            >
              Delete
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
