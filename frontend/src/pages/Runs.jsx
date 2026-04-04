import { useEffect, useState } from 'react';
import { PlayCircle, Download, RefreshCw, ChevronRight } from 'lucide-react';
import toast from 'react-hot-toast';
import { getRepricerRuns, exportRun } from '../api/client';
import { formatMsk } from '../utils/date';
import RunDetailModal from '../components/RunDetailModal';
import { StatusBadge } from '../components/StatusBadge';

export default function Runs() {
  const [runs, setRuns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedRun, setSelectedRun] = useState(null);

  useEffect(() => {
    loadRuns();
  }, []);

  const loadRuns = async () => {
    setLoading(true);
    try {
      const response = await getRepricerRuns({ limit: 50 });
      setRuns(response.data.runs);
    } catch {
      toast.error('Ошибка загрузки запусков');
    } finally {
      setLoading(false);
    }
  };

  const handleExport = async (runId) => {
    try {
      const response = await exportRun(runId);
      const url = window.URL.createObjectURL(new Blob([response.data]));
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', `run_${runId}.xlsx`);
      document.body.appendChild(link);
      link.click();
      link.remove();
      toast.success('Файл загружен');
    } catch {
      toast.error('Ошибка экспорта');
    }
  };

  const getTypeLabel = (type) => {
    if (type === 'scheduled') return 'Авто';
    if (type === 'manual') return 'Ручной';
    return type;
  };

  return (
    <>
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="page-h1">Запуски репрайсера</h1>
          <p className="text-sm mt-1 text-muted-foreground">История запусков и результаты</p>
        </div>
        <button onClick={loadRuns} className="btn btn-secondary gap-2">
          <RefreshCw className="w-4 h-4" />
          Обновить
        </button>
      </div>

      {loading ? (
        <div className="flex items-center justify-center h-64">
          <div className="spinner" />
        </div>
      ) : runs.length === 0 ? (
        <div className="card text-center py-16">
          <PlayCircle className="w-10 h-10 mx-auto mb-3" style={{ color: '#e5e7eb' }} />
          <p style={{ color: 'var(--text-3)' }}>Запуски не найдены</p>
        </div>
      ) : (
        <div className="card overflow-hidden p-0">
          <div className="overflow-x-auto">
            <table className="dark-table">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Тип</th>
                  <th>Статус</th>
                  <th>Товаров</th>
                  <th>Обновлено</th>
                  <th>Пропущено</th>
                  <th>Ошибок</th>
                  <th>Запущен</th>
                  <th style={{ textAlign: 'right' }}>Действия</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <tr
                    key={run.id}
                    onClick={() => setSelectedRun(run)}
                    style={{ cursor: 'pointer' }}
                  >
                    <td>
                      <span className="font-semibold text-foreground">#{run.id}</span>
                    </td>
                    <td>{getTypeLabel(run.run_type)}</td>
                    <td><StatusBadge status={run.status} /></td>
                    <td className="font-medium text-foreground">{run.total_products}</td>
                    <td className="font-semibold text-green-600">{run.updated_count}</td>
                    <td className="text-muted-foreground">{run.skipped_count}</td>
                    <td className="font-medium text-red-500">{run.error_count}</td>
                    <td>{formatMsk(run.started_at, 'dd.MM.yyyy HH:mm')}</td>
                    <td>
                      <div className="flex justify-end items-center gap-1.5">
                        <button
                          onClick={(e) => { e.stopPropagation(); handleExport(run.id); }}
                          className="btn btn-xs btn-secondary"
                          title="Скачать отчет"
                        >
                          <Download className="w-3.5 h-3.5" />
                        </button>
                        <ChevronRight className="w-4 h-4" style={{ color: 'var(--text-3)' }} />
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>

    {selectedRun && (
      <RunDetailModal run={selectedRun} onClose={() => setSelectedRun(null)} />
    )}
    </>
  );
}
