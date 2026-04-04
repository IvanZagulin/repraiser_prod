import { Badge } from '@/components/ui/badge';

export function StatusBadge({ status }) {
  if (status === 'completed') return <Badge variant="success">Завершён</Badge>;
  if (status === 'running')   return <Badge variant="info">Выполняется</Badge>;
  if (status === 'failed')    return <Badge variant="destructive">Ошибка</Badge>;
  return <Badge variant="secondary">{status}</Badge>;
}
