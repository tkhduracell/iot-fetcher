import React from 'react';

const RefreshBadge: React.FC = () => {
  const reloadPage = () => {
    window.location.reload()
  }

  return (
    <button
      onClick={reloadPage}
      title="Reload page"
      className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-1.5 rounded-full shadow text-sm font-semibold cursor-pointer flex items-center gap-1.5"
    >
      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
      </svg>
      Reload
    </button>
  )
}

export default RefreshBadge;
