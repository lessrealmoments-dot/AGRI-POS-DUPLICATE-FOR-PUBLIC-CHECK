/**
 * CategorySelect — dynamic org-aware category selector.
 * Loads categories from GET /api/products/categories.
 * Provides inline "Add new category" option.
 * Usage:
 *   <CategorySelect value={form.category} onChange={v => setForm({...form, category: v})} />
 */
import { useState, useEffect } from 'react';
import { api } from '../contexts/AuthContext';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select';
import { Input } from './ui/input';
import { Button } from './ui/button';
import { Plus, Trash2, Tag } from 'lucide-react';
import { toast } from 'sonner';

const ADD_NEW_SENTINEL = '__add_new__';

export default function CategorySelect({ value, onChange, testId, disabled }) {
  const [categories, setCategories] = useState([]);
  const [addingNew, setAddingNew] = useState(false);
  const [newName, setNewName] = useState('');
  const [saving, setSaving] = useState(false);

  const loadCategories = () => {
    api.get('/products/categories').then(r => setCategories(r.data || [])).catch(() => {});
  };

  useEffect(() => { loadCategories(); }, []);

  const handleSelect = (v) => {
    if (v === ADD_NEW_SENTINEL) {
      setAddingNew(true);
      setNewName('');
    } else {
      onChange(v);
    }
  };

  const confirmNew = async () => {
    const name = newName.trim();
    if (!name) return;
    setSaving(true);
    try {
      await api.post('/products/categories', { name });
      loadCategories();
      onChange(name);
      setAddingNew(false);
      setNewName('');
      toast.success(`Category "${name}" added`);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to add category');
    }
    setSaving(false);
  };

  const deleteCategory = async (cat, e) => {
    e.stopPropagation();
    if (!window.confirm(`Delete category "${cat}"? This will fail if products still use it.`)) return;
    try {
      await api.delete(`/products/categories/${encodeURIComponent(cat)}`);
      loadCategories();
      if (value === cat) onChange('');
      toast.success(`Category "${cat}" deleted`);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to delete category');
    }
  };

  if (addingNew) {
    return (
      <div className="flex items-center gap-1.5">
        <Input
          autoFocus
          value={newName}
          onChange={e => setNewName(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') confirmNew(); if (e.key === 'Escape') setAddingNew(false); }}
          placeholder="New category name"
          className="h-9 flex-1"
          data-testid="new-category-input"
        />
        <Button size="sm" onClick={confirmNew} disabled={saving || !newName.trim()} className="h-9 px-3 bg-[#1A4D2E] hover:bg-[#15412a]"
          data-testid="new-category-confirm">
          {saving ? '...' : <Plus size={14} />}
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setAddingNew(false)} className="h-9 px-2 text-slate-500">
          ✕
        </Button>
      </div>
    );
  }

  return (
    <Select value={value} onValueChange={handleSelect} disabled={disabled}>
      <SelectTrigger data-testid={testId || 'product-category-input'} className="h-9">
        <SelectValue placeholder="Select category" />
      </SelectTrigger>
      <SelectContent>
        {categories.length === 0 && (
          <div className="px-3 py-2 text-xs text-slate-400 italic">No categories yet</div>
        )}
        {categories.map(cat => (
          <SelectItem key={cat} value={cat}>
            <span className="flex items-center justify-between gap-2 w-full">
              <span className="flex items-center gap-1.5">
                <Tag size={11} className="text-slate-400" />
                {cat}
              </span>
            </span>
          </SelectItem>
        ))}
        <div className="border-t border-slate-100 mt-1 pt-1">
          <SelectItem value={ADD_NEW_SENTINEL}>
            <span className="flex items-center gap-1.5 text-emerald-700 font-medium">
              <Plus size={13} /> Add new category…
            </span>
          </SelectItem>
        </div>
      </SelectContent>
    </Select>
  );
}
