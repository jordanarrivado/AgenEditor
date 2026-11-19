/** Browser Demo Mode route for the Production workspace. All data is in-memory simulation. */

import React, { useMemo } from 'react';
import { useAppData } from '../../app/AppDataContext';
import { createDemoProductionRepository } from '../../mocks/productionDemo';
import { ProductionWorkspace } from './ProductionWorkspace';

export const ProductionPage: React.FC = () => {
  const { projects } = useAppData();
  const repository = useMemo(() => createDemoProductionRepository(), []);
  return (
    <div className="flex h-full min-h-0 flex-col">
      <ProductionWorkspace repository={repository} projects={projects.map(({ id, name }) => ({ id, name }))} />
    </div>
  );
};
