import React from 'react';
import { queryReloadInterval } from '../globals';


const RefreshBadge: React.FC = () => {
  const reloadPage = () => {
    window.location.reload()
  }

  return (
    <button
      onClick={reloadPage}
      title="Reload page"
      className="bg-blue-600 hover:bg-blue-700 text-white px-3 py-1 rounded-full shadow text-sm font-semibold cursor-pointer"
    >
      Reload
    </button>
  )
}

export default RefreshBadge;
