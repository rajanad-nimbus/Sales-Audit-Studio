import { redirect } from 'next/navigation';

/** The Data Pipeline screen is now Imports; this keeps old bookmarks and links working. */
export default function PipelineRedirect() {
  redirect('/imports');
}
