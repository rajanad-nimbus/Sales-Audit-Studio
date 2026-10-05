interface User {
  id: number;
  username: string;
  email: string;
  full_name: string | null;
  created_at: string;
  updated_at: string;
}

interface UserListProps {
  users: User[];
  onEdit: (user: User) => void;
  onDelete: (id: number) => void;
}

export function UserList({ users, onEdit, onDelete }: UserListProps) {
  return (
    <div className="table-container">
      <table className="data-table">
        <thead>
          <tr>
            <th>Username</th>
            <th>Email</th>
            <th>Full Name</th>
            <th>Created</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {users.map((user) => (
            <tr key={user.id}>
              <td className="font-medium">{user.username}</td>
              <td>{user.email}</td>
              <td>{user.full_name || '—'}</td>
              <td className="text-secondary text-sm">
                {new Date(user.created_at).toLocaleDateString()}
              </td>
              <td>
                <div className="action-buttons">
                  <button
                    className="btn-small btn-secondary"
                    onClick={() => onEdit(user)}
                  >
                    Edit
                  </button>
                  <button
                    className="btn-small btn-danger"
                    onClick={() => onDelete(user.id)}
                  >
                    Delete
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
