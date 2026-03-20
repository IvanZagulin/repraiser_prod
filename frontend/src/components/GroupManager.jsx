import { useState, useEffect } from 'react';
import { Plus, X, Tag } from 'lucide-react';
import toast from 'react-hot-toast';
import { getGroups, createGroup, deleteGroup } from '../api/client';

const COLOR_PRESETS = ['#7c3aed', '#3b82f6', '#16a34a', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899'];

export default function GroupManager({ selectedGroupId, onSelectGroup }) {
  const [groups, setGroups] = useState([]);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState('');
  const [newColor, setNewColor] = useState(COLOR_PRESETS[0]);

  const fetchGroups = () => {
    setLoading(true);
    getGroups()
      .then((res) => setGroups(res.data))
      .catch(() => toast.error('Не удалось загрузить группы'))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    fetchGroups();
  }, []);

  const handleCreate = () => {
    const name = newName.trim();
    if (!name) {
      toast.error('Введите название группы');
      return;
    }
    createGroup({ name, color: newColor })
      .then(() => {
        toast.success('Группа создана');
        setNewName('');
        setNewColor(COLOR_PRESETS[0]);
        setCreating(false);
        fetchGroups();
      })
      .catch((err) => {
        toast.error(err.response?.data?.detail || 'Ошибка создания группы');
      });
  };

  const handleDelete = (e, group) => {
    e.stopPropagation();
    if (!window.confirm(`Удалить группу "${group.name}"?`)) return;
    deleteGroup(group.id)
      .then(() => {
        toast.success('Группа удалена');
        if (selectedGroupId === group.id) onSelectGroup(null);
        fetchGroups();
      })
      .catch((err) => {
        toast.error(err.response?.data?.detail || 'Ошибка удаления группы');
      });
  };

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-2 mb-1">
        <Tag className="w-4 h-4 text-muted-foreground" />
        <span className="text-sm font-medium text-foreground">Группы</span>
      </div>

      {/* All products */}
      <button
        onClick={() => onSelectGroup(null)}
        className={`flex items-center gap-2 px-2 py-1.5 rounded-md text-sm transition-colors w-full text-left ${
          selectedGroupId === null
            ? 'bg-primary/10 text-primary font-medium'
            : 'text-muted-foreground hover:bg-muted'
        }`}
      >
        Все товары
      </button>

      {/* Group list */}
      {loading ? (
        <div className="text-xs text-muted-foreground px-2 py-1">Загрузка...</div>
      ) : (
        groups.map((group) => (
          <button
            key={group.id}
            onClick={() => onSelectGroup(group.id)}
            className={`flex items-center gap-2 px-2 py-1.5 rounded-md text-sm transition-colors w-full text-left group ${
              selectedGroupId === group.id
                ? 'bg-primary/10 text-primary font-medium'
                : 'text-muted-foreground hover:bg-muted'
            }`}
          >
            <span
              className="w-2.5 h-2.5 rounded-full shrink-0"
              style={{ backgroundColor: group.color || '#6b7280' }}
            />
            <span className="truncate flex-1">{group.name}</span>
            {group.product_count != null && (
              <span className="text-xs opacity-60">{group.product_count}</span>
            )}
            <X
              className="w-3.5 h-3.5 opacity-0 group-hover:opacity-60 hover:!opacity-100 shrink-0 transition-opacity"
              onClick={(e) => handleDelete(e, group)}
            />
          </button>
        ))
      )}

      {/* Create new group */}
      {creating ? (
        <div className="flex flex-col gap-2 p-2 rounded-md border border-border bg-card mt-1">
          <input
            type="text"
            placeholder="Название группы"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
            className="w-full px-2 py-1 text-sm rounded border border-border bg-background text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
            autoFocus
          />
          <div className="flex items-center gap-1.5">
            {COLOR_PRESETS.map((c) => (
              <button
                key={c}
                onClick={() => setNewColor(c)}
                className={`w-5 h-5 rounded-full border-2 transition-transform ${
                  newColor === c ? 'border-foreground scale-110' : 'border-transparent'
                }`}
                style={{ backgroundColor: c }}
              />
            ))}
          </div>
          <div className="flex gap-1.5">
            <button
              onClick={handleCreate}
              className="flex-1 px-2 py-1 text-xs rounded bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
            >
              Создать
            </button>
            <button
              onClick={() => { setCreating(false); setNewName(''); }}
              className="px-2 py-1 text-xs rounded text-muted-foreground hover:bg-muted transition-colors"
            >
              Отмена
            </button>
          </div>
        </div>
      ) : (
        <button
          onClick={() => setCreating(true)}
          className="flex items-center gap-1.5 px-2 py-1.5 rounded-md text-xs text-muted-foreground hover:bg-muted transition-colors mt-1"
        >
          <Plus className="w-3.5 h-3.5" />
          Новая группа
        </button>
      )}
    </div>
  );
}
