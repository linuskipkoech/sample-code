import { Injectable } from '@angular/core';
import { RedistModalService, OllibsService, AlertService, RestlibsService } from 'src/app/services';
import * as utils01 from '../utils/utils01';
import { OLDistrict } from 'src/app/utils/ol-district';

/**
 * Interface for district statistics data
 */
interface DistrictStatistics {
  header: string[];
  columns: any[];
  displayedColumns: string[];
  dataSource: any[];
}

/**
 * Interface for compliance values
 */
interface ComplianceValues {
  contiguity: boolean;
  popdeviation: boolean;
  subassign: boolean;
}

/**
 * Interface for selection update values
 */
interface SelectionUpdateValues {
  makeChange: {
    selectedFeatures: any[];
    previousSelectedFeatures: any[];
  };
}

/**
 * Interface for district overlap data
 */
interface DistrictOverlap {
  available: any[];
  highlight: any[];
  losing: { [key: string]: any[] };
}

/**
 * Service responsible for geographic operations and redistricting calculations.
 * Handles statistics calculations, district modifications, and compliance checks.
 */
@Injectable({
  providedIn: 'root'
})
export class GeoOperationsService {
  // Core dependencies
  private mapObj: any = null;
  private kcuser: any = null;
  private statisticsGroup: string = '';

  // Table configuration for statistics display
  private columns: Array<any> = [];
  private displayedHeaders: string[] = [];
  private displayedColumns: Array<any> = [];
  private dataSource: any = null;

  // Geographic and compliance tracking
  private geoChangeValue: string | null = null;
  private complianceValue: ComplianceValues = {
    contiguity: false,
    popdeviation: false,
    subassign: false
  };

  constructor(
    private redistService: RedistModalService,
    private ollibs: OllibsService,
    private restlibs: RestlibsService,
    public alertService: AlertService
  ) {}

  /**
   * Initialize service with required objects
   * @param values Object containing kcuser and mapObj
   */
  setAllObjects(values: { kcobj: any; mapObj: any }): void {
    this.kcuser = values.kcobj;
    this.mapObj = values.mapObj;
  }

  /**
   * Configure statistics dialog for table display
   */
  setStatisticsDialog(): void {
    const tabData: any[] = [];
    const statisticsGroup = this.redistService.getStatisticsGroup();
    const statistics = this.kcuser.statistics['StatisticsGroups'][statisticsGroup];

    this.displayedHeaders = ['Value'];

    // Load layer fields as columns
    const layerFields = statistics.LayerFields;
    for (const field of layerFields) {
      this.displayedHeaders.push(field.Alias);
    }

    // Load calculated fields as columns
    const calculatedFields = statistics.CalculatedFields;
    for (const field of calculatedFields) {
      this.displayedHeaders.push(field.Alias);
    }

    // Initialize table rows based on statistics headers
    for (const header of statistics.Headers) {
      if (header !== 'Parameter') {
        const row: any = {};
        for (const headerKey of this.displayedHeaders) {
          if (headerKey === 'Value') {
            row[headerKey] = header.replace('Value', '').trim();
          } else {
            row[headerKey] = '0';
          }
        }
        if (Object.keys(row).length !== 0) {
          tabData.push(row);
        }
      }
    }

    // Generate column definitions for mat-table
    const columns = this.generateColumnDefinitions(tabData);
    this.columns = columns;
    this.displayedColumns = this.columns.map(c => c.columnDef);
    this.dataSource = tabData;
  }

  /**
   * Generate column definitions for mat-table
   * @param tabData Table data array
   * @returns Array of column definitions
   */
  private generateColumnDefinitions(tabData: any[]): any[] {
    const columns = tabData
      .reduce((columns, row) => [...columns, ...Object.keys(row)], [])
      .reduce((columns, column) => 
        columns.includes(column) ? columns : [...columns, column], []
      );

    return columns.map(column => ({
        columnDef: column,
        header: column.replaceAll('_', ' '),
      cell: (element: any) => `${element[column] ? element[column] : ''}`
    }));
  }

  /**
   * Get statistics dialog configuration
   * @returns Statistics dialog object
   */
  getStatisticsDialog(): DistrictStatistics {
    return {
      header: this.displayedHeaders,
      columns: this.columns,
      displayedColumns: this.displayedColumns,
      dataSource: this.dataSource
    };
  }

  /**
   * Update percentage fields when statistics group changes
   */
  updatePercentFields(): void {
    const updateFeatList = this.redistService.retrieveWorkDistrictData(true);
    const workLayer = this.redistService.getWorkDistrictLayer();

    this.updatePercentFieldsGeo(workLayer, updateFeatList, this.redistService.getStatisticsGroup());

    const selectionValues = this.updateSelectionCounts(
      this.redistService.getSelectionDict(),
      this.redistService.getActionMode(),
      true,
      this.redistService.getStatisticsGroup(),
      true
    );

    this.loadResultsIntoDialog(selectionValues, 0, null);
  }

  /**
   * Update percentage fields for geographic features
   * @param workLayer The work district layer
   * @param featList List of features to update
   * @param statisticsGroup Statistics group name
   */
  updatePercentFieldsGeo(workLayer: any, featList: any[], statisticsGroup: string): void {
    if (!workLayer || !statisticsGroup || featList.length < 1) {
      return;
    }

    const calculateFields = this.kcuser.statistics['StatisticsGroups'][statisticsGroup]['CalculatedFields'];
    const changedFeat: any = {};

    for (const workFeat of featList) {
      const updateFeat = workFeat.getProperties();
      
      for (const fieldConfig of calculateFields) {
        const fieldName = fieldConfig['Field'];
        const fieldNameLower = fieldName.toLowerCase();

        if (fieldNameLower in updateFeat && fieldConfig['FormulaType'] === 'Percent') {
          const newValue = this.calculatePercentageValue(fieldConfig, updateFeat);
          if (newValue !== null) {
            changedFeat[fieldNameLower] = newValue;
          }
        }
      }
    }

    if (Object.keys(changedFeat).length > 0) {
      featList[0].setProperties(changedFeat);
    }
  }

  /**
   * Calculate percentage value for a field
   * @param fieldConfig Field configuration object
   * @param updateFeat Feature properties
   * @returns Calculated percentage value
   */
  private calculatePercentageValue(fieldConfig: any, updateFeat: any): number | null {
    const formulaFields = fieldConfig['FormulaFields'].split(',');
    const numeratorField = formulaFields[0];
    const denominatorField = formulaFields[1];
    const offset = formulaFields.length === 3 ? parseFloat(formulaFields[2]) : 0;

    // Get denominator
    let denominator: number;
    if (denominatorField.toLowerCase() in updateFeat) {
      denominator = updateFeat[denominatorField.toLowerCase()];
              } else {
      denominator = this.convertIdealPopulation(denominatorField);
    }

    if (denominator <= 0) {
      return 0;
    }

    // Calculate numerator
    let numerator: number;
    if (numeratorField.indexOf('+') !== -1) {
      const numeratorFields = numeratorField.split('+');
      numerator = numeratorFields.reduce((sum, field) => 
        sum + parseFloat(updateFeat[field.toLowerCase()] || 0), 0
      );
    } else {
      numerator = parseFloat(updateFeat[numeratorField.toLowerCase()] || 0);
    }

    const percentage = (numerator / denominator * 100) + offset;
    return parseFloat(percentage.toFixed(2));
  }

  /**
   * Update selection counts for statistics calculation
   * @param selFeatures Selected features
   * @param action Action type (add, remove, New District)
   * @param calcPercent Whether to calculate percentages
   * @param statisticsGroup Statistics group name
   * @param allFields Whether to include all fields
   * @returns Updated selection values
   */
  updateSelectionCounts(
    selFeatures: any,
    action: string,
    calcPercent: boolean = true,
    statisticsGroup: string = 'allFields',
    allFields: boolean = false
  ): any {
    const sumDict: any = {};
    const percentDict: any = {};
    const jsonData = this.kcuser.statistics;
    const workFeatureSelected = this.redistService.getWorkFeatureSelected();

    try {
      // Initialize sum dictionary based on field configuration
      this.initializeSumDictionary(sumDict, jsonData, statisticsGroup, allFields);

      // Initialize percentage dictionary if needed
      if (calcPercent && statisticsGroup !== '') {
        this.initializePercentDictionary(percentDict, jsonData, statisticsGroup);
      }

      // Process selected features
      this.processSelectedFeatures(selFeatures, sumDict, action);

      // Calculate percentages if requested
      if (calcPercent) {
        this.calculatePercentages(sumDict, percentDict);
      }

    } catch (error) {
      console.error('Error in updateSelectionCounts:', error);
    }

    this.redistService.setSelectionValues(sumDict);
    return sumDict;
  }

  /**
   * Initialize sum dictionary with field configurations
   */
  private initializeSumDictionary(sumDict: any, jsonData: any, statisticsGroup: string, allFields: boolean): void {
    if (allFields) {
      for (const field of jsonData['StatisticsGroups']['allFields']['LayerFields']) {
        sumDict[field] = 0;
      }
    } else if (statisticsGroup !== '') {
      for (const field of jsonData['StatisticsGroups'][statisticsGroup]['LayerFields']) {
        if (field['FormulaType'] === 'Sum') {
          sumDict[field['Field']] = 0;
        }
      }
    }
  }

  /**
   * Initialize percentage dictionary
   */
  private initializePercentDictionary(percentDict: any, jsonData: any, statisticsGroup: string): void {
    for (const field of jsonData['StatisticsGroups'][statisticsGroup]['CalculatedFields']) {
      if (field['FormulaType'] === 'Percent') {
        percentDict[field['Field']] = field['FormulaFields'];
      }
    }
  }

  /**
   * Process selected features and update sum dictionary
   */
  private processSelectedFeatures(selFeatures: any, sumDict: any, action: string): void {
    if (typeof selFeatures === 'object') {
      for (const [selKey, selVal] of Object.entries(selFeatures)) {
        const features = selVal as any[];
        for (const feature of features) {
          for (const [key, val] of Object.entries(sumDict)) {
            const currentValue = val as number;
            const featureValue = feature.getProperties()[key.toLowerCase()] || 0;

            if (['add', 'New District'].includes(action)) {
              sumDict[key] = currentValue + featureValue;
            } else if (action === 'remove') {
              sumDict[key] = currentValue - Math.abs(featureValue);
            }
          }
        }
      }
    }
  }

  /**
   * Calculate percentage values
   */
  private calculatePercentages(sumDict: any, percentDict: any): void {
        this.redistService.setPercentFieldFormulas(percentDict);
    
    for (const [key, formula] of Object.entries(percentDict)) {
      const fieldsList = (formula as string).split(',');
      const denominator = this.getDenominator(fieldsList[1], sumDict);
      
      if (denominator !== 0) {
        const numerator = this.calculateNumerator(fieldsList[0], sumDict);
        const offset = fieldsList.length === 3 ? parseFloat(fieldsList[2]) : 0;
        sumDict[key] = (numerator / denominator * 100) + offset;
      } else {
        sumDict[key] = 0;
      }
    }
  }

  /**
   * Get denominator value for percentage calculation
   */
  private getDenominator(denominatorField: string, sumDict: any): number {
    if (denominatorField in sumDict) {
      return parseFloat(sumDict[denominatorField]);
            } else {
      return this.convertIdealPopulation(denominatorField);
    }
  }

  /**
   * Calculate numerator value for percentage calculation
   */
  private calculateNumerator(numeratorField: string, sumDict: any): number {
    if (numeratorField.indexOf('+') !== -1) {
      const numeratorFields = numeratorField.split('+');
      return numeratorFields.reduce((sum, field) => sum + parseFloat(sumDict[field] || 0), 0);
    } else {
      return parseFloat(sumDict[numeratorField] || 0);
    }
  }

  /**
   * Load calculated results into statistics dialog
   * @param sumDict Sum dictionary with calculated values
   * @param tableWidgetIndex Table widget index
   * @param selectedDistrictFeat Selected district feature
   */
  loadResultsIntoDialog(sumDict: any, tableWidgetIndex: number = 0, selectedDistrictFeat: any = null): any {
    const statisticsGroup = this.redistService.getStatisticsGroup();
    const statistics = this.kcuser.statistics['StatisticsGroups'][statisticsGroup];

    if (!statisticsGroup) {
      return;
    }

    try {
      const selectedFeat = this.getSelectedDistrictFeature(selectedDistrictFeat);
      const selectedFeatProps = selectedFeat ? selectedFeat.getProperties() : null;
      const cfeat = this.getDistrictFeatureId(selectedFeat);

      this.setStatisticsDialog();

      // Process calculated fields
      this.processCalculatedFields(sumDict, statistics.CalculatedFields, selectedFeat, selectedFeatProps, cfeat);

      // Process layer fields
      this.processLayerFields(sumDict, statistics.LayerFields, selectedFeat, selectedFeatProps, cfeat);

      // Handle zero population edge case
      this.handleZeroPopulationCase();

      // Refresh statistics panel if open
      if (this.redistService.getStatisticsPanel() !== '') {
        this.redistService.componentRedistServiceStatisticsCall({ refreshData: '' });
      }

    } catch (error) {
      console.error('Error in loadResultsIntoDialog:', error);
    }

    return -1;
  }

  /**
   * Get selected district feature
   */
  private getSelectedDistrictFeature(selectedDistrictFeat: any): any {
    if (selectedDistrictFeat === null) {
      return this.redistService.getSelectedDistrictFeat();
    } else if (typeof selectedDistrictFeat === 'string') {
      const workLayer = this.redistService.getWorkDistrictLayer();
      return workLayer.getSource().getFeatureById(selectedDistrictFeat);
            } else {
      return selectedDistrictFeat;
    }
  }

  /**
   * Get district feature ID
   */
  private getDistrictFeatureId(selectedFeat: any): string {
    if (selectedFeat) {
      return selectedFeat.getId().split('.')[1];
    }
    return '0';
  }

  /**
   * Process calculated fields for statistics dialog
   */
  private processCalculatedFields(
    sumDict: any,
    calculatedFields: any[],
    selectedFeat: any,
    selectedFeatProps: any,
    cfeat: string
  ): void {
    for (const fieldConfig of calculatedFields) {
      if (sumDict.hasOwnProperty(fieldConfig['Field'])) {
        const unitString = fieldConfig['Units'] || '';
        const labelKey = fieldConfig['Alias'].toString();

        // Selected area value
        this.assignToDatasource(
          selectedFeat,
          labelKey,
          'Selected Area',
          sumDict[fieldConfig['Field']].toFixed(2) + ' ' + unitString
        );

        // Current value
        const currentValue = this.calculateCurrentValue(selectedFeat, fieldConfig['Field'], cfeat, unitString);
        this.assignToDatasource(selectedFeat, labelKey, 'Current', currentValue);

        // Proposed value
        const proposedValue = this.calculateProposedValue(
          sumDict,
          fieldConfig,
          selectedFeat,
          selectedFeatProps,
          cfeat,
          unitString
        );
        this.assignToDatasource(selectedFeat, labelKey, 'Proposed', proposedValue);
      }
    }
  }

  /**
   * Process layer fields for statistics dialog
   */
  private processLayerFields(
    sumDict: any,
    layerFields: any[],
    selectedFeat: any,
    selectedFeatProps: any,
    cfeat: string
  ): void {
    for (const fieldConfig of layerFields) {
      if (sumDict.hasOwnProperty(fieldConfig['Field'])) {
        const unitString = fieldConfig['Units'] || '';
        const labelKey = fieldConfig['Alias'].toString();

        // Selected area value
        this.assignToDatasource(
          selectedFeat,
          labelKey,
          'Selected Area',
          sumDict[fieldConfig['Field']].toFixed(2) + ' ' + unitString
        );

        if (selectedFeat && cfeat !== '0') {
          // Current value
          const currentValue = selectedFeatProps[fieldConfig['Field'].toLowerCase()] + ' ' + unitString;
          this.assignToDatasource(selectedFeat, labelKey, 'Current', currentValue);

          // Proposed value
          const proposedValue = this.calculateLayerProposedValue(
            sumDict,
            fieldConfig,
            selectedFeatProps,
            unitString
          );
          this.assignToDatasource(selectedFeat, labelKey, 'Proposed', proposedValue);
        }
      }
    }
  }

  /**
   * Calculate current value for calculated fields
   */
  private calculateCurrentValue(selectedFeat: any, fieldName: string, cfeat: string, unitString: string): string {
    if (selectedFeat && cfeat !== '0') {
      const percentVal = this.calcFeaturePercentValues(selectedFeat, fieldName);
      return percentVal.toFixed(2) + ' ' + unitString;
    } else {
      return '0 ' + unitString;
    }
  }

  /**
   * Calculate proposed value for calculated fields
   */
  private calculateProposedValue(
    sumDict: any,
    fieldConfig: any,
    selectedFeat: any,
    selectedFeatProps: any,
    cfeat: string,
    unitString: string
  ): string {
    const formulaFields = fieldConfig['FormulaFields'].split(',');
    const numerator = this.calculateProposedNumerator(formulaFields[0], sumDict, selectedFeat, selectedFeatProps, cfeat);
    const denominator = this.calculateProposedDenominator(formulaFields[1], sumDict, selectedFeat, selectedFeatProps, cfeat);
    const offset = formulaFields.length === 3 ? parseFloat(formulaFields[2]) : 0;

    const percentVal = this.calcPercentValues(numerator, denominator, offset);
    return percentVal.toFixed(2) + ' ' + unitString;
  }

  /**
   * Calculate proposed numerator
   */
  private calculateProposedNumerator(
    numeratorField: string,
    sumDict: any,
    selectedFeat: any,
    selectedFeatProps: any,
    cfeat: string
  ): number {
    if (numeratorField.indexOf('+') !== -1) {
      const numeratorFields = numeratorField.split('+');
      return numeratorFields.reduce((sum, field) => {
        if (selectedFeat && cfeat !== '0') {
          return sum + selectedFeatProps[field.toLowerCase()] + sumDict[field];
            } else {
          return sum + sumDict[field];
        }
      }, 0);
    } else {
      if (selectedFeat && cfeat !== '0') {
        return selectedFeatProps[numeratorField.toLowerCase()] + sumDict[numeratorField];
      } else {
        return sumDict[numeratorField];
      }
    }
  }

  /**
   * Calculate proposed denominator
   */
  private calculateProposedDenominator(
    denominatorField: string,
    sumDict: any,
    selectedFeat: any,
    selectedFeatProps: any,
    cfeat: string
  ): number {
    if (Object.keys(sumDict).indexOf(denominatorField) === -1) {
      return this.convertIdealPopulation(denominatorField);
            } else {
      if (selectedFeat && cfeat !== '0') {
        return selectedFeatProps[denominatorField.toLowerCase()] + sumDict[denominatorField];
      } else {
        return sumDict[denominatorField];
      }
    }
  }

  /**
   * Calculate proposed value for layer fields
   */
  private calculateLayerProposedValue(
    sumDict: any,
    fieldConfig: any,
    selectedFeatProps: any,
    unitString: string
  ): string {
    const fieldName = fieldConfig['Field'];
    const currentValue = selectedFeatProps[fieldName.toLowerCase()];
    const sumValue = sumDict[fieldName];

    if (sumValue === 0) {
      return '0.00 ' + unitString;
    } else {
      return (currentValue + sumValue) + ' ' + unitString;
    }
  }

  /**
   * Handle zero population edge case
   */
  private handleZeroPopulationCase(): void {
      const dataSource = this.redistService.getDistrictTabData();
    for (const [key, value] of Object.entries(dataSource)) {
      const dsrc = (value as any)['dataSource'];
      for (let idx = 0; idx < dsrc.length; idx++) {
        if (dsrc[idx]['Value'] === 'Proposed') {
            const popval = parseInt(dsrc[idx]['Population'], 10);
          if (popval === 0) {
            for (const [dkey, dval] of Object.entries(dsrc[idx])) {
              if (!['Value', 'Population'].includes(dkey)) {
                if (dkey === '%Deviation') {
                  dsrc[idx][dkey] = '-100.00 %';
                } else {
                  dsrc[idx][dkey] = '0.00 %';
                }
              }
            }
          }
        }
      }
    }
  }

  /**
   * Calculate percentage values for a feature
   * @param feature OpenLayers feature
   * @param percentDictKey Key for percentage calculation
   * @returns Calculated percentage value
   */
  calcFeaturePercentValues(feature: any, percentDictKey: string): number {
    const percentFieldFormulas = this.redistService.getPercentFieldFormulas();
    const featureProps = feature.getProperties();

    if (Object.keys(percentFieldFormulas).length === 0) {
      return 0;
    }

    try {
      const formulaFields = percentFieldFormulas[percentDictKey].split(',');
      const denominator = this.getFeatureDenominator(formulaFields[1], featureProps);
      
      if (denominator <= 0) {
        return 0;
      }

      const numerator = this.getFeatureNumerator(formulaFields[0], featureProps);
      const offset = formulaFields.length === 3 ? parseFloat(formulaFields[2]) : 0;

      return (numerator / denominator * 100) + offset;
    } catch (error) {
      console.error('Error calculating feature percent values:', error);
      return 0;
    }
  }

  /**
   * Get denominator for feature calculation
   */
  private getFeatureDenominator(denominatorField: string, featureProps: any): number {
    if (denominatorField.toLowerCase() in featureProps) {
      return parseFloat(featureProps[denominatorField.toLowerCase()]);
          } else {
      return this.convertIdealPopulation(denominatorField);
    }
  }

  /**
   * Get numerator for feature calculation
   */
  private getFeatureNumerator(numeratorField: string, featureProps: any): number {
    if (numeratorField.indexOf('+') !== -1) {
      const numeratorFields = numeratorField.split('+');
      return numeratorFields.reduce((sum, field) => 
        sum + parseFloat(featureProps[field.toLowerCase()] || 0), 0
      );
    } else {
      return parseFloat(featureProps[numeratorField.toLowerCase()] || 0);
    }
  }

  /**
   * Calculate percentage values
   * @param countVal Count value
   * @param denominator Denominator value
   * @param deviationFactor Optional deviation factor
   * @returns Calculated percentage
   */
  calcPercentValues(countVal: number, denominator: number, deviationFactor: number | null = null): number {
    try {
      if (denominator > 0) {
        const basePercentage = (countVal / denominator) * 100;
        return deviationFactor !== null ? basePercentage + deviationFactor : basePercentage;
      }
      return 0;
    } catch (error) {
      console.error('Error calculating percent values:', error);
      return 0;
    }
  }

  /**
   * Clear all redistricting layer selections
   * @param clearSelectionDict Whether to clear selection dictionary
   * @param clearOriginalSelection Whether to clear original selection
   * @param action Action type
   */
  clearAllRedistrictingLayerSelections(
    clearSelectionDict: boolean = true,
    clearOriginalSelection: boolean = false,
    action: string
  ): number {
    if (clearSelectionDict) {
      this.ollibs.clearSelectShiftClickFeatures();
      this.redistService.setSelectionDict({});
      this.redistService.setPreviousSelectionIds([]);
      
      if (this.redistService.getStatisticsGroup() !== '') {
        const selectionValues = this.updateSelectionCounts(
          this.redistService.getSelectionDict(),
          action,
          true,
          this.redistService.getStatisticsGroup(),
          true
        );
          this.loadResultsIntoDialog(selectionValues);
      }
    }

    if (clearOriginalSelection) {
      this.redistService.setOriginalSelectionIds([]);
    }

    return 1;
  }

  /**
   * Convert ideal population value to number
   * @param value Value to convert
   * @returns Converted value
   */
  convertIdealPopulation(value: any): number {
    if (value === 'self.idealPopulation') {
      return this.redistService.getIdealPopulation();
    } else {
      try {
        return parseFloat(value);
      } catch (error) {
          console.warn('Failed to parse value:', value);
        return 0;
      }
    }
  }

  /**
   * Assign value to data source for statistics table
   * @param selectedDistrictFeat District feature
   * @param labelKey Column key
   * @param labelRow Row key
   * @param value Value to assign
   */
  assignToDatasource(selectedDistrictFeat: any, labelKey: string, labelRow: string, value: any): void {
    try {
      const districtTabData = this.redistService.getDistrictTabData();
      let dataSource = null;

      if (selectedDistrictFeat) {
        dataSource = districtTabData[selectedDistrictFeat.getId()]?.dataSource;
      }

      if (dataSource) {
        for (let idx = 0; idx < dataSource.length; idx++) {
          if (dataSource[idx]['Value'] === labelRow) {
            dataSource[idx][labelKey] = value;
            break;
          }
        }
      }
    } catch (error) {
      console.error('Error assigning to datasource:', error);
    }
  }

  /**
   * Update all selections based on values
   * @param values Selection update values
   * @param flag Special flag for update operations
   */
  selectionUpdateAll(values: SelectionUpdateValues, flag: string = ''): void {
    let selectedFeatures: any[] = [];
    let previousSelectedFeatures: any = null;
    let districtOverlap: DistrictOverlap | null = null;

    if ('makeChange' in values) {
      selectedFeatures = values.makeChange.selectedFeatures;
      previousSelectedFeatures = values.makeChange.previousSelectedFeatures;
      districtOverlap = this.ollibs.getDistrictOverlap();
    }

    const action = this.redistService.getActionMode() || this.redistService.getSelectedDistRecord().districtname;
    this.createDistrictTab(districtOverlap);

    if (districtOverlap) {
      this.processDistrictOverlap(districtOverlap, action, flag);
    }
  }

  /**
   * Process district overlap for selection updates
   */
  private processDistrictOverlap(districtOverlap: DistrictOverlap, action: string, flag: string): void {
    const { available, highlight, losing } = districtOverlap;
    const currentDistrict = this.redistService.getSelectedDistrictFeat();
    const districtTabData = this.redistService.getDistrictTabData();

    for (const [selKey, selVal] of Object.entries(districtTabData)) {
      if (selKey === currentDistrict.getId()) {
        this.subSelectionUpdateAll(action, highlight, selKey, flag);
      } else if (selKey in losing) {
        this.subSelectionUpdateAll('remove', losing[selKey], selKey, flag);
      }
    }
  }

  /**
   * Sub-function for selection updates
   * @param action Action type
   * @param selectedFeatures Selected features
   * @param selKey District key
   * @param flag Special flag
   */
  subSelectionUpdateAll(action: string, selectedFeatures: any[], selKey: string, flag: string): void {
    const selectionData = this.updateSelectionGeomAndDict(action, this.redistService.getSelectedDistrictFeat(), true, selectedFeatures);
    
    this.redistService.setSelectionDict(selectionData.selectionDict);
    this.redistService.setSelectionGeometry(selectionData.selectionGeometry);

    this.updateSelectionCounts(
      this.redistService.getSelectionDict(),
      action,
      true,
      this.redistService.getStatisticsGroup(),
      true
    );

    if (flag === 'updatePlanValues') {
      this.updatePlanValuesForFeature(action, selectedFeatures, selKey);
    } else {
      this.loadResultsIntoDialog(this.redistService.getSelectionValues(), 0, selKey);
      this.clearAllRedistrictingLayerSelections(false, false, action);
    }
  }

  /**
   * Update plan values for a specific feature
   */
  private updatePlanValuesForFeature(action: string, selectedFeatures: any[], selKey: string): void {
      const workDistrictLayer = this.redistService.getWorkDistrictLayer();
      const srcfeat = workDistrictLayer.getSource().getFeatureById(selKey);

    if (action === 'remove') {
      const unionResult = OLDistrict.unionSelectedFeats(selectedFeatures);
      const tgtfeat = OLDistrict.removeArea(srcfeat, unionResult.jstsobj);
        OLDistrict.updateJSTSOLGeom(srcfeat, tgtfeat.jstsobj);
        this.redistService.setDirtyDistrict(srcfeat);
      }

    this.updatePlanValues(
      workDistrictLayer,
      action,
      srcfeat,
      this.redistService.getSelectionValues(),
      this.redistService.getStatisticsGroup()
    );
  }

  /**
   * Update selection geometry and dictionary
   * @param action Action type
   * @param feat District feature
   * @param originalSelectionUpdate Whether this is an original selection update
   * @param selectedFeatures Selected features
   * @returns Selection data object
   */
  updateSelectionGeomAndDict(
    action: string,
    feat: any,
    originalSelectionUpdate: boolean = true,
    selectedFeatures: any[] = []
  ): { selectionDict: any; selectionGeometry: any } {
    const outret = { selectionDict: null, selectionGeometry: null };

    try {
      const workLayer = this.redistService.getWorkDistrictLayer();
      if (!workLayer) {
        return outret;
      }

      const baseGeoLayer = this.getBaseGeoLayer();
      if (!baseGeoLayer) {
        return outret;
      }

      const selectionDict: any = {};
      let selectionGeometry = null;

      const features = selectedFeatures.length > 0 ? selectedFeatures : this.ollibs.getSelectedFeatures();

      if (originalSelectionUpdate) {
        this.redistService.setPreviousSelectionIds(features);
      }

      // Create union of selected features
      const unionResult = OLDistrict.unionSelectedFeats(features);
      if (unionResult.error === null) {
        selectionGeometry = unionResult.jstsobj;
      }

      selectionDict[baseGeoLayer.get('name')] = features;
            outret.selectionDict = selectionDict;
            outret.selectionGeometry = selectionGeometry;

    } catch (error) {
      console.error('Error in updateSelectionGeomAndDict:', error);
    }

    return outret;
  }

  /**
   * Get base geographic layer
   */
  private getBaseGeoLayer(): any {
    const dlayer = this.ollibs.getCurrentDistrictLayer();
    if (!dlayer) {
      return null;
    }
    return utils01.findLayerBy('name', dlayer, this.ollibs.getMapObj());
  }

  /**
   * Update plan values for a district
   * @param workLayer Work district layer
   * @param editAction Edit action type
   * @param workFeat Work feature
   * @param updateValues Values to update
   * @param statisticsGroup Statistics group
   */
  updatePlanValues(
    workLayer: any,
    editAction: string,
    workFeat: any,
    updateValues: any,
    statisticsGroup: string = 'allFields'
  ): void {
    const layerFields = this.kcuser.statistics['StatisticsGroups']['allFields']['LayerFields'];
    const updateFeat = workFeat.getProperties();
    const changedFeat: any = {};

    // Calculate all regular fields
    for (const fieldName of layerFields) {
      const fieldNameLower = fieldName.toLowerCase();
      if (fieldNameLower in updateFeat && fieldName in updateValues) {
        let newVal = 0;
        
        switch (editAction) {
          case 'add':
          newVal = Math.round(updateFeat[fieldNameLower] + updateValues[fieldName]);
            break;
          case 'remove':
          newVal = Math.round(updateFeat[fieldNameLower] - Math.abs(updateValues[fieldName]));
            break;
          case 'New District':
          newVal = Math.abs(Math.round(updateValues[fieldName]));
            break;
        }
        
        changedFeat[fieldNameLower] = newVal;
      }
    }

    workFeat.setProperties(changedFeat);
    this.updatePercentFieldsGeo(workLayer, [workFeat], this.redistService.getStatisticsGroup());
  }

  /**
   * Create district tab for statistics dialog
   * @param districtOverlap District overlap data
   */
  createDistrictTab(districtOverlap: DistrictOverlap | null): void {
    if (!districtOverlap) {
      districtOverlap = { available: [], highlight: [], losing: {} };
    }

    const districtTabData: any = {};
    const { available, highlight, losing } = districtOverlap;
    const stdlg = this.getStatisticsDialog();
    const currDistrict = this.redistService.getSelectedDistrictFeat();

    // Current district
    const currentDistrictData = JSON.parse(JSON.stringify(stdlg));
    districtTabData[currDistrict.getId()] = currentDistrictData;
    districtTabData[currDistrict.getId()]['columns'] = [...stdlg.columns];

    // Losing districts
    for (const [selKey, selVal] of Object.entries(losing)) {
      const losingDistrictData = JSON.parse(JSON.stringify(stdlg));
      districtTabData[selKey] = losingDistrictData;
      districtTabData[selKey]['columns'] = [...stdlg.columns];
    }

    this.redistService.setDistrictTabData(districtTabData);
  }

  /**
   * Get statistics data as object for save/submit operations
   * @returns Statistics object
   */
  getStatisticsIntoObj(): any {
    const outret: any = {};
    const distLayer = this.redistService.getWorkDistrictLayer();
    const dstab = this.redistService.getDistrictTabs();
    const popData = this.redistService.getPopulationData();

    for (const pidx of popData) {
      outret[pidx.key] = [];
      this.redistService.setStatisticsGroup(pidx.key);
      
      for (const feat of dstab) {
        const srcfeat = distLayer.getSource().getFeatureById(feat.fid);
        const cfeat = feat.fid.split('.');
        
        if (cfeat[1] !== '0' && srcfeat !== null) {
          this.redistService.setSelectionDict({});
          this.redistService.setSelectedDistrictFeat(srcfeat);
          this.updateSelectionCounts(
            this.redistService.getSelectionDict(),
            'add',
            true,
            this.redistService.getStatisticsGroup(),
            true
          );
          this.createDistrictTab(null);
          this.loadResultsIntoDialog(
            this.redistService.getSelectionValues(),
            0,
            this.redistService.getSelectedDistrictFeat()
          );
          
          const dst = this.redistService.getDistrictTabData();
          for (const [selKey, selVal] of Object.entries(dst)) {
            const ds: any = selVal;
            for (const dsidx of ds.dataSource) {
              if (dsidx.Value === 'Current') {
                dsidx['fid'] = feat.fid;
                dsidx['districtname'] = feat.districtname;
                outret[pidx.key].push(dsidx);
              }
            }
          }
        }
      }
    }
    
    return outret;
  }

  /**
   * Check if zoom level change requires make change
   * @param layer Layer name
   * @returns Change detection result
   */
  isRequireMakeChange(layer: string): { isChanged: boolean; currentZoom: number; oldValue: string | null } {
    const czoom = this.ollibs.getMapObj().getView().getZoom();
    const ccenter = this.ollibs.getMapObj().getView().getCenter();
    const outret = { isChanged: false, currentZoom: czoom, oldValue: this.geoChangeValue };

    if (this.geoChangeValue === null) {
      this.geoChangeValue = `${layer}|${czoom}|${ccenter}`;
    } else {
      const oval = this.geoChangeValue.split('|');
      if (layer !== oval[0] && this.ollibs.getSelectedFeatures().length > 0) {
        this.geoChangeValue = `${layer}|${czoom}|${ccenter}`;
        outret.isChanged = true;
      } else {
        this.geoChangeValue = `${layer}|${czoom}|${ccenter}`;
      }
    }

    return outret;
  }

  /**
   * Check contiguity of districts
   * @returns Array of non-contiguous districts
   */
  contiguityCheck(): Array<{ fid: string; districtname: string; part: number }> {
    const outret: Array<{ fid: string; districtname: string; part: number }> = [];
    const feats = this.redistService.retrieveWorkDistrictData(false);

    for (const feat of feats) {
      const cid = feat.getId().split('.');
      if (cid[1] !== '0') {
        const geom = feat.getGeometry();
        if (geom.getType() === 'MultiPolygon' && geom.getCoordinates().length > 1) {
          const vprop = feat.getProperties();
          outret.push({
            fid: feat.getId(),
            districtname: vprop['districtname'],
            part: geom.getCoordinates().length
          });
        }
      }
    }

    return outret;
  }

  /**
   * Check block assignment asynchronously
   * @param ybody Request body
   * @returns Promise with assignment check result
   */
  async assignCheck(ybody: any): Promise<any> {
    const xbody = {
      page: ybody.page,
      layer: ybody.layer,
      plan_name: ybody.plan_name,
      gjson: JSON.stringify(ybody.gjson),
      sessid: ybody.sessid
    };
    
    const surl = '/redist/block_assign/';
    return await this.restlibs.requestAJAXPromise(
      surl,
      'POST',
      this.restlibs.json_header,
      'json',
      [],
      JSON.stringify(xbody)
    );
  }

  /**
   * Set compliance values
   * @param value Compliance values object
   */
  setComplianceValue(value: ComplianceValues): void {
    this.complianceValue = value;
  }

  /**
   * Get compliance values
   * @returns Compliance values object
   */
  getComplianceValue(): ComplianceValues {
    return this.complianceValue;
  }
}