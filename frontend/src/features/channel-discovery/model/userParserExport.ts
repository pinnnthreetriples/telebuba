import { userParserExportUrl, type UserParserExportFormat } from '@/entities/user-parser';

// Скачивание — обычный переход по ссылке: сервер отдаёт файл с `attachment`, страница
// остаётся на месте, а большая база не собирается в памяти окна.
export function downloadExport(runId: string, format: UserParserExportFormat) {
  window.location.assign(userParserExportUrl(runId, format));
}
